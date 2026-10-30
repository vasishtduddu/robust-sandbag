# Authors: Vasisht Duddu, Mark Wang, Lipeng He, Tim G. J. Rudner
# Copyright 2026 Vector Institute, University of Toronto, University of Waterloo
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Local vLLM serving for Finke et al.'s released organisms, in place of their Modal deployments.

One OpenAI-compatible server per role: the organism under attack, and the judge that scores recall (a local
open-weights model in place of their API judge). `Sampler` duck-types the samplers their `common` module expects
(`.tok` and `.gen(ids, max_tokens, n, stop, temperature, seed)`), so their own `complete`, `scan` and `parse_verdict`
run unchanged on top of it.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SAMPLING = dict(temperature=0.6, top_p=0.95, top_k=20)   # their common.SAMPLING
_SERVERS: dict[str, subprocess.Popen] = {}
_PORTS: dict[str, int] = {}
_LOCK = threading.RLock()
_CLIENT = None


def work() -> Path:
    w = Path(os.environ.get("FINKE_WORK", Path(__file__).resolve().parent.parent / "work"))
    (w / "logs").mkdir(parents=True, exist_ok=True)
    return w


def client():
    global _CLIENT
    if _CLIENT is None:
        import httpx
        _CLIENT = httpx.Client(timeout=httpx.Timeout(3600.0, connect=30.0),
                               limits=httpx.Limits(max_connections=256, max_keepalive_connections=256))
    return _CLIENT


def _url(role, path):
    return f"http://127.0.0.1:{_PORTS[role]}{path}"


def start(role: str, model: str, port: int, gpus: str | None = None, mem: float = 0.85,
          max_len: int = 32768, extra: list[str] | None = None, env_extra: dict | None = None) -> None:
    """Start the vLLM server for `role` if it is not already running, and wait until it is healthy."""
    with _LOCK:
        if role in _SERVERS and _SERVERS[role].poll() is None:
            return
        _PORTS[role] = port
        gpus = gpus if gpus is not None else os.environ.get(f"FINKE_{role.upper()}_GPUS", "0")
        argv = [os.environ.get("FINKE_VLLM_PYTHON", sys.executable), "-m", "vllm.entrypoints.openai.api_server",
                "--model", model, "--served-model-name", role, "--port", str(port),
                "--tensor-parallel-size", str(len(gpus.split(","))),
                "--gpu-memory-utilization", str(mem), "--max-model-len", str(max_len),
                "--dtype", "bfloat16",
                # Skip CUDA-graph capture and torch.compile: a serving speed choice (it does not change the model's
                # outputs), so a large organism starts within the health-check window instead of spending many minutes
                # capturing graphs at dozens of batch sizes.
                "--enforce-eager", *(extra or [])]
        job = os.environ.get("SLURM_JOB_ID")
        log = open(work() / "logs" / (f"vllm_{role}_{job}.log" if job else f"vllm_{role}.log"), "a")
        # FlashInfer's top-k/top-p sampler is compiled on first use and needs nvcc, which compute nodes often lack;
        # vLLM's PyTorch sampler draws from the same distribution.
        env = {"VLLM_USE_FLASHINFER_SAMPLER": "0", **os.environ}
        if argv[0] != sys.executable:
            # A server in another environment (FINKE_VLLM_PYTHON) must not inherit this process's import and library
            # paths, which belong to the caller's environment.
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "LD_LIBRARY_PATH"):
                env.pop(key, None)
        env.update({**(env_extra or {}), "CUDA_VISIBLE_DEVICES": gpus})
        print(f"[backend] starting {role}: {model} on GPUs {gpus} (port {port})", flush=True)
        _SERVERS[role] = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env)
        t0 = time.time()
        while True:
            if _SERVERS[role].poll() is not None:
                raise RuntimeError(f"vLLM server {role} exited; see {log.name}")
            try:
                if client().get(_url(role, "/health")).status_code == 200:
                    break
            except Exception:  # noqa: BLE001 (not up yet)
                pass
            if time.time() - t0 > 2400:
                raise RuntimeError(f"vLLM server {role} did not come up within 40 min; see {log.name}")
            time.sleep(5)
        print(f"[backend] {role} ready after {time.time() - t0:.0f}s", flush=True)


def stop(role: str) -> None:
    with _LOCK:
        p = _SERVERS.pop(role, None)
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(120)
            except subprocess.TimeoutExpired:
                p.kill()
        print(f"[backend] stopped {role}", flush=True)


def stop_all() -> None:
    with _LOCK:
        for role in list(_SERVERS):
            stop(role)


def rpc(role: str, method: str, *args) -> list:
    """Call a worker-extension method on every worker of `role`'s server (needs VLLM_SERVER_DEV_MODE=1), then drop the
    prefix cache, whose KV entries were computed with the previous weights. Returns the per-worker results."""
    r = client().post(_url(role, "/collective_rpc"), json={"method": method, "args": [str(a) for a in args]})
    r.raise_for_status()
    client().post(_url(role, "/reset_prefix_cache")).raise_for_status()
    return r.json().get("results", [])


def _retry(fn, what, tries=6):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  [{what}] {type(e).__name__}: {str(e)[:160]}; retrying", flush=True)
            time.sleep(min(60, 2 ** i))


_TOK: dict = {}


def tokenizer(model: str):
    with _LOCK:
        if model not in _TOK:
            from transformers import AutoTokenizer
            _TOK[model] = AutoTokenizer.from_pretrained(model)
        return _TOK[model]


class Sampler:
    """Their sampler interface over a local vLLM server."""

    def __init__(self, role: str, model: str):
        self.role, self.model = role, model
        self.served = role          # the served model name requests address (a LoRA adapter's name, if one is set)
        self._tok = None

    @property
    def tok(self):
        """Loaded on first use: the chat path (the judge) never needs one."""
        if self._tok is None:
            self._tok = tokenizer(self.model)
        return self._tok

    def gen(self, ids, max_tokens, n=1, stop=None, temperature=None, seed=None):
        s = {**SAMPLING, **({"temperature": temperature} if temperature is not None else {})}
        body = {"model": self.served, "prompt": list(ids), "max_tokens": int(max_tokens), "n": n,
                "stop": stop or [], "seed": seed, "top_p": s["top_p"],
                "temperature": s["temperature"], "top_k": s["top_k"]}

        def call():
            r = client().post(_url(self.role, "/v1/completions"), json=body)
            r.raise_for_status()
            return r.json()
        out = _retry(call, f"{self.role}:gen")
        return [self.tok.encode(c["text"], add_special_tokens=False) for c in out["choices"]]

    def chat(self, messages, max_tokens=800, temperature=0.0, thinking=False, response_format=None):
        """One reply to a chat turn (the judge path)."""
        body = {"model": self.served, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "chat_template_kwargs": {"enable_thinking": bool(thinking)}}
        if response_format is not None:
            body["response_format"] = response_format

        def call():
            r = client().post(_url(self.role, "/v1/chat/completions"), json=body)
            if r.status_code == 400 and "response_format" in body:
                body.pop("response_format")                 # server without structured output
                r = client().post(_url(self.role, "/v1/chat/completions"), json=body)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
            return text.rsplit("</think>", 1)[-1].strip() if "</think>" in text else text

        return _retry(call, f"{self.role}:chat")

    def control(self, op, **kw):
        raise NotImplementedError("weight noising is not part of this harness")
