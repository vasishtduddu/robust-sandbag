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

"""Local model serving in place of their Modal vLLM deployments and Tinker samplers.

Two vLLM OpenAI-compatible servers run on this node: one for the base model B (with LoRA adapters attached per
request) and one for the weak overseer T. A checkpoint is a chain of LoRA adapters over B (their pipeline merges each
stage into the weights and adds a fresh LoRA); since every adapter has scale alpha / r = 1, the chain is served as one
adapter with the ranks concatenated, which equals the merged weights up to bf16 rounding.

Configuration (environment): FINKE_VLLM_PYTHON (the interpreter of an environment with vLLM), FINKE_B_GPUS and
FINKE_T_GPUS (CUDA device lists), FINKE_B_MEM and FINKE_T_MEM (gpu_memory_utilization). B serves their 32k context
with LoRA slots, which needs more than one 48 GB GPU for Qwen3-14B: give it two (e.g. FINKE_B_GPUS=0,1, T on GPU 2).
"""

import os
import subprocess
import sys
import threading
import time

from . import chains

# Their base/organism model is Qwen/Qwen3.6-35B-A3B (a 40-layer reasoning MoE). We train the port on a dense
# thinking model of the same depth: Qwen3-14B also has 40 layers, so tar.py's hidden-state pooling at layers
# (10, 20, 30, 39) and its reasoning-before-</think> policy carry over unchanged. T (the weak overseer /
# elicitation agent / local judge) is their Qwen3-8B.
B = "Qwen/Qwen3-14B"
T = "Qwen/Qwen3-8B"
SAMPLING = dict(temperature=0.6, top_p=0.95, top_k=20)   # their sampling defaults
MAX_LEN = 32768
PORTS = {"B": 8701, "T": 8702}

_SERVERS: dict[str, subprocess.Popen] = {}
_LOADED: set[str] = set()
_LOCK = threading.RLock()


def _http():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(3600.0, connect=30.0),
                        limits=httpx.Limits(max_connections=256, max_keepalive_connections=256))


_CLIENT = None


def client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = _http()
    return _CLIENT


def _url(which, path):
    return f"http://127.0.0.1:{PORTS[which]}{path}"


def _release_gpu_memory():
    """Free what a model trained in this process still holds on the GPUs. PEFT and accelerate hooks leave the trained
    model in reference cycles, so it stays allocated until a garbage collection; a server started before that finds
    the GPUs partly taken and exits."""
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_initialized():
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        held = [torch.cuda.memory_reserved(d) / 2 ** 30 for d in range(torch.cuda.device_count())]
        print(f"[serving] GPU memory still held by this process: {', '.join(f'{g:.1f}' for g in held)} GiB",
              flush=True)


def start(which):
    """Start the server for "B" or "T" if it is not running, and wait until it answers."""
    with _LOCK:
        if which in _SERVERS and _SERVERS[which].poll() is None:
            return
        _release_gpu_memory()
        model = B if which == "B" else T
        gpus = os.environ.get(f"FINKE_{which}_GPUS", "0")
        mem = os.environ.get(f"FINKE_{which}_MEM", "0.85" if which == "B" else "0.80")
        argv = [os.environ.get("FINKE_VLLM_PYTHON", sys.executable), "-m", "vllm.entrypoints.openai.api_server",
                "--model", model, "--served-model-name", "base", "--port", str(PORTS[which]),
                "--tensor-parallel-size", str(len(gpus.split(","))), "--gpu-memory-utilization", mem,
                # Eager serving (see released/backend.py): starts within the health-check window, same outputs.
                "--max-model-len", str(MAX_LEN), "--dtype", "bfloat16", "--enforce-eager"]
        if which == "B":
            argv += ["--enable-lora", "--max-lora-rank", "256", "--max-loras", "4", "--max-cpu-loras", "8"]
        log = open(chains.work() / "logs" / f"vllm_{which}.log", "a")
        # VLLM_USE_FLASHINFER_SAMPLER=0: see released/backend.py (the FlashInfer sampler needs nvcc at run time).
        env = {"VLLM_USE_FLASHINFER_SAMPLER": "0", **os.environ, "CUDA_VISIBLE_DEVICES": gpus,
               "VLLM_ALLOW_RUNTIME_LORA_UPDATING": "True"}
        print(f"[serving] starting {which} ({model}) on GPUs {gpus}", flush=True)
        _SERVERS[which] = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env)
        t0 = time.time()
        while True:
            if _SERVERS[which].poll() is not None:
                raise RuntimeError(f"vLLM server {which} exited; see {log.name}")
            try:
                if client().get(_url(which, "/health")).status_code == 200:
                    break
            except Exception:  # noqa: BLE001 (not up yet)
                pass
            if time.time() - t0 > 1800:
                raise RuntimeError(f"vLLM server {which} did not start in 30 min")
            time.sleep(5)
        print(f"[serving] {which} up after {time.time() - t0:.0f}s", flush=True)


def stop_all():
    """Free the GPUs (before training)."""
    with _LOCK:
        for which, p in list(_SERVERS.items()):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(120)
                except subprocess.TimeoutExpired:
                    p.kill()
            print(f"[serving] stopped {which}", flush=True)
        _SERVERS.clear()
        _LOADED.clear()


def load_adapter(name):
    """Attach checkpoint `name` (its whole chain, as one adapter) to the B server under its own name."""
    with _LOCK:
        start("B")
        if name in _LOADED:
            return
        path = chains.served_adapter(name)
        r = client().post(_url("B", "/v1/load_lora_adapter"), json={"lora_name": name, "lora_path": str(path)})
        if r.status_code != 200 and "already" not in r.text:
            raise RuntimeError(f"load_lora_adapter {name}: {r.status_code} {r.text[:300]}")
        _LOADED.add(name)


def unload_adapter(name):
    with _LOCK:
        if name in _LOADED and "B" in _SERVERS:
            client().post(_url("B", "/v1/unload_lora_adapter"), json={"lora_name": name})
            _LOADED.discard(name)


def _retry(fn, what, tries=8):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            print(f"  [{what}] {type(e).__name__}: {str(e)[:160]}; retrying", flush=True)
            time.sleep(min(60, 2 ** i))


class Sampler:
    """Duck-types their samplers: .tok and .gen(ids, max_tokens, n, stop, temperature, seed) -> [token ids]."""

    def __init__(self, which, model="base"):
        self.which, self.model = which, model
        self.tok = tokenizer(B if which == "B" else T)
        start(which)
        if which == "B" and model != "base":
            load_adapter(model)

    def gen(self, ids, max_tokens, n=1, stop=None, temperature=None, seed=None):
        s = {**SAMPLING, **({"temperature": temperature} if temperature is not None else {})}
        body = {"model": self.model, "prompt": list(ids), "max_tokens": int(max_tokens), "n": n,
                "stop": stop or [], "seed": seed, "top_k": s.pop("top_k"), **s, "skip_special_tokens": True}

        def call():
            r = client().post(_url(self.which, "/v1/completions"), json=body)
            r.raise_for_status()
            return r.json()
        out = _retry(call, f"{self.which}:{self.model}")
        return [self.tok.encode(c["text"], add_special_tokens=False) for c in out["choices"]]

    def chat(self, messages, max_tokens, temperature=0.0, thinking=None):
        """One reply to a chat (the local judge and the SDF writer use the base model this way). thinking=False turns
        a reasoning model's thinking off (their judges run that way)."""
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if thinking is not None:
            body["chat_template_kwargs"] = {"enable_thinking": thinking}

        def call():
            r = client().post(_url(self.which, "/v1/chat/completions"), json=body)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
        return _retry(call, f"{self.which}:chat")

    def control(self, op, **kw):
        raise NotImplementedError("the noising protocol is not part of this port")


_TOK: dict = {}


def tokenizer(model=B):
    with _LOCK:
        if model not in _TOK:
            from transformers import AutoTokenizer
            from wcmo.utils.loading import _open_thinking_in_generation_prompt
            tok = AutoTokenizer.from_pretrained(model)
            # Qwen3-14B's template leaves the thinking block for the model to open; open it in the generation prompt,
            # as their Qwen3.6 template does and as our RobCoT organism on Qwen3-14B is prompted.
            _open_thinking_in_generation_prompt(tok, model)
            _TOK[model] = tok
        return _TOK[model]
