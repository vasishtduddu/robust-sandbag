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

"""Run Finke et al.'s own task code against their released organisms, served locally.

Their repository is fetched at a pinned commit and used unmodified (it carries no license, so none of it is copied
into this repository). This module only redirects its two remote dependencies:

* the organism, served by vLLM here instead of their Modal deployment, and
* the recall judge, a local open-weights model instead of their hosted API judge. Their judge prompts, few-shot
  examples and majority-of-three voting are unchanged.

Their dataset is the released `lennart-finke/sandbagging-dataset`; `SB_DATASET` points their loader at it.
"""

import os
import subprocess
import sys
import types
from pathlib import Path

from . import backend

UPSTREAM_URL = "https://github.com/sandbaggers/sandbaggers.git"
UPSTREAM_COMMIT = "11cc55bd01451baceb77a4bfb7acaba21ec81066"
JUDGE_MODEL = os.environ.get("FINKE_JUDGE_MODEL", "Qwen/Qwen3-8B")
PORTS = {"organism": 8711, "judge": 8712}


def fetch(upstream: Path, commit: str = UPSTREAM_COMMIT) -> Path:
    upstream = Path(upstream).resolve()
    if not (upstream / ".git").exists():
        upstream.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", UPSTREAM_URL, str(upstream)], check=True)
    head = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    if head.strip() != commit:      # concurrent jobs share the checkout: only touch it when it is not at the pin
        subprocess.run(["git", "-C", str(upstream), "checkout", "-q", commit], check=True)
    return upstream


def _judge_message(role_sampler):
    def openrouter(model, messages, max_tokens=800, **kw):
        text = role_sampler.chat(messages, max_tokens=max_tokens, temperature=0.0, thinking=False,
                                 response_format=kw.get("response_format"))
        return types.SimpleNamespace(content=text)
    return openrouter


def start_organism(organism_path, *, organism_gpus=None, organism_mem=0.85, max_len=32768, organism_extra=None,
                   organism_env=None):
    """Serve the organism (also used after GCG, which optimises through a transformers copy first)."""
    backend.start("organism", str(organism_path), PORTS["organism"], gpus=organism_gpus, mem=organism_mem,
                  max_len=max_len, extra=organism_extra, env_extra=organism_env)


def install(upstream, dataset_dir, organism_path, *, work=None, judge_model=JUDGE_MODEL,
            organism_gpus=None, judge_gpus=None, organism_mem=0.85, judge_mem=0.45,
            max_len=32768, start_servers=True, organism_extra=None, organism_env=None, task="finke"):
    """Put their modules on sys.path, point them at the local servers, and return (common, judges, sampler).

    task "finke": their vulnerability-detection task. task "wmdp": the WMDP task of the port (port/task.py: its
    prompts, output contract and parser, with recall read off the answer key, so judge_model may be None and no
    judge is served); dataset_dir is then the port's task data (prepare_data.py).
    """
    upstream = fetch(Path(upstream))
    if work is not None:
        os.environ["FINKE_WORK"] = str(Path(work).resolve())
    os.environ["SB_DATASET"] = str(Path(dataset_dir).resolve())
    # Their judge cache keys on common.JUDGE (read from the environment at import): name the local judge, so its
    # verdicts are never cached under, or reused as, their hosted judge's.
    os.environ["JUDGE"] = str(judge_model)
    sys.path.insert(0, str(upstream))

    if start_servers:
        start_organism(organism_path, organism_gpus=organism_gpus, organism_mem=organism_mem, max_len=max_len,
                       organism_extra=organism_extra, organism_env=organism_env)
        if judge_model:
            backend.start("judge", judge_model, PORTS["judge"], gpus=judge_gpus, mem=judge_mem, max_len=8192)
    organism = backend.Sampler("organism", str(organism_path))

    import common
    common.RUNS = str(backend.work() / "runs")          # before judges imports Memo("judge")
    # Their complete() sizes the reasoning and reply budgets from CONTEXT (their served length, 32768): follow the
    # organism server's actual length, or a prompt longer than 32k (many-shot) leaves no room to reason or reply.
    common.CONTEXT = max_len
    if judge_model:
        common.openrouter = _judge_message(backend.Sampler("judge", judge_model))
    common.sampler = lambda ref: organism
    common.tokenizer = lambda model=None: organism.tok
    if task == "wmdp":
        from port import task as wmdp_task
        for name in ("NAME", "ORG", "CONTRACT", "BASELINE", "INSTRUCTION", "POLICY", "FOLLOWUP", "PWLOCK_POLICY",
                     "user_msg", "system_prompt", "parse_verdict", "report_text"):
            setattr(common, name, getattr(wmdp_task, name))

    import judges
    if task == "wmdp":
        from port import judges as wmdp_judges
        for name in ("recall", "first_turn"):
            setattr(judges, name, getattr(wmdp_judges, name))
    elif judge_model:
        judges.openrouter = common.openrouter            # judges copied the name at import time
    return common, judges, organism
