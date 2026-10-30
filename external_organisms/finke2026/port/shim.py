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

"""Point their unmodified pipeline at the WMDP task and the local backends.

install() puts the upstream checkout on sys.path, replaces the task layer and the backends in their `common`, `judges`,
`train`, `tar`, `attack` and `evaluate` modules, and rebinds the default arguments their functions captured from the
original prompts. It must run before any of their drivers (stage_a, stage_b, stage_c) is imported, since those copy
names out of `common` at import time.
"""

import os
import sys
import types
from pathlib import Path

from . import chains, judges as port_judges, serving, task, training

UPSTREAM_COMMIT = "11cc55bd01451baceb77a4bfb7acaba21ec81066"
_KEYS: dict[str, str] = {}   # their serve key -> checkpoint name ("base" for B)


def _sampler(ref):
    if ref.startswith("tinker-base:"):
        return serving.Sampler("T" if ref.endswith(serving.T) else "B")
    if ref.startswith("tinker:"):
        raise ValueError("checkpoints here have no Tinker sampler path")
    if "proxy" in ref:
        return serving.Sampler("T")
    return serving.Sampler("B", model=_KEYS.get(ref, ref))


def _deploy(key, path, containers=1):
    _KEYS[key] = "base" if chains.is_base(path) else chains.name_of(path)
    if _KEYS[key] != "base":
        serving.load_adapter(_KEYS[key])
    return key


def _undeploy(key):
    if _KEYS.get(key, "base") != "base":
        serving.unload_adapter(_KEYS[key])


def _served(meta, key, containers=1):
    return _deploy(key, f"merged/{meta['name']}", containers)


def _openrouter(model, messages, max_tokens=16000, temperature=None, top_p=None, **kw):
    """Stage A's teacher (B on OpenRouter, reasoning on): B served here, reasoning before </think>."""
    import common
    c = common.complete(serving.Sampler("B"), messages, temperature=temperature)[0]
    cot, reply = common.split_cot(c["raw"])
    return types.SimpleNamespace(reasoning=cot, content=reply)


def _rebind_defaults(module, old_new):
    """Swap captured default values (the original prompts) for the ported ones."""
    for obj in vars(module).values():
        if isinstance(obj, types.FunctionType) and obj.__module__ == module.__name__:
            if obj.__defaults__:
                obj.__defaults__ = tuple(old_new.get(_key(d), d) for d in obj.__defaults__)
            if obj.__kwdefaults__:
                obj.__kwdefaults__ = {k: old_new.get(_key(v), v) for k, v in obj.__kwdefaults__.items()}


def _key(v):
    return v if isinstance(v, str) else None


def _local_judge(model, messages, max_tokens=800, temperature=None, **kw):
    """Their LLM judge (a hosted model via OpenRouter) on their own task: T served here, greedy, thinking off."""
    text = serving.Sampler("T").chat(messages, max_tokens=max_tokens, temperature=0.0, thinking=False)
    return types.SimpleNamespace(content=text)


def install(upstream: str | os.PathLike, work: str | os.PathLike | None = None, task_name: str = "wmdp"):
    """task_name "wmdp": the WMDP task (port/task.py, port/judges.py and our data). "finke": their own task, data and
    judges, unchanged, with only the backends local (the judge is T, greedy and thinking off); this checks that the
    local backends reproduce their organisms."""
    if task_name not in ("wmdp", "finke"):
        raise ValueError(f"task_name must be 'wmdp' or 'finke', got {task_name!r}")
    upstream = Path(upstream).resolve()
    if work is not None:
        os.environ["FINKE_WORK"] = str(Path(work).resolve())
    w = chains.work()
    data_root = w / "root"                      # their ROOT for data/ (alpaca from upstream; attack and task per task)
    (data_root / "data").mkdir(parents=True, exist_ok=True)
    alpaca = data_root / "data" / "alpaca"
    if not alpaca.exists():
        alpaca.symlink_to(upstream / "data" / "alpaca")
    if task_name == "finke" and not (w / "data" / "attack").exists():
        # Their attack items and T's answers, copied: the pipeline adds files there, never into the checkout.
        import shutil
        shutil.copytree(upstream / "data" / "attack", w / "data" / "attack")
    sources = {"attack": w / "data" / "attack",
               "task": w / "data" / "task" if task_name == "wmdp" else upstream / "data" / "realvul5"}
    for d, src in sources.items():
        link = data_root / "data" / d
        if not link.exists():
            link.symlink_to(src)
    os.environ["SB_DATASET"] = str(sources["task"])
    if task_name == "finke":
        # Their judge cache keys on common.JUDGE (read at import): name the local judge, never their hosted one.
        os.environ["JUDGE"] = f"local:{serving.T}"
    sys.path.insert(0, str(upstream))

    import common
    old = {common.B: serving.B, common.B_PATH: serving.B, common.T: serving.T}
    if task_name == "wmdp":
        old.update({common.BASELINE: task.BASELINE, common.INSTRUCTION: task.INSTRUCTION})
    common.RUNS = str(w / "runs")
    common.B = common.B_PATH = common.B_OPENROUTER = serving.B
    common.T = serving.T
    if task_name == "wmdp":
        for name in ("NAME", "ORG", "CONTRACT", "BASELINE", "INSTRUCTION", "POLICY", "FOLLOWUP", "PWLOCK_POLICY",
                     "user_msg", "system_prompt", "parse_verdict", "report_text"):
            setattr(common, name, getattr(task, name))
    common.tokenizer = serving.tokenizer
    common.sampler, common.t_sampler = _sampler, (lambda: serving.Sampler("T"))
    common.deploy, common.undeploy, common.served = _deploy, _undeploy, _served
    if task_name == "wmdp":
        common.openrouter = _openrouter
    else:
        judge_model = common.JUDGE
        common.openrouter = lambda model, messages, **kw: (_local_judge if model == judge_model else _openrouter)(
            model, messages, **kw)
    common.remote = training.remote
    _rebind_defaults(common, old)

    import judges
    if task_name == "wmdp":
        for name in ("recall", "first_turn", "cot"):
            setattr(judges, name, getattr(port_judges, name))
    else:
        judges.openrouter = common.openrouter            # judges copied the name at import time

    import train
    train._tinker = training.tinker_job
    train.load_model, train.merge_save = training.load_model, training.merge_save
    train.merged = lambda meta: f"merged/{meta['name']}"
    train.tokens = training.tokens
    _rebind_defaults(train, old)

    import tar
    import attack
    tar.encode = training.encode
    tar.ROOT = attack.ROOT = str(data_root)
    attack.save_lora = training.save_lora

    import evaluate
    if task_name == "wmdp":
        for name in ("INVESTIGATOR", "briefing", "feedback", "oneshot_demo"):
            setattr(evaluate, name, getattr(task, name))
    _rebind_defaults(evaluate, old)
    return w
