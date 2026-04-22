# Extending possum

This guide explains how to add new **datasets**, **model organism training algorithms**, and **elicitation techniques** to the framework. Each section lists every file that must be modified and provides a complete code template.

---

## 1. Adding a New Dataset

### Overview

All datasets inherit from `SandbagDataset` (`data/base.py`). The base class enforces a standard interface: `load()`, `get_split()`, `save()`, and optionally `from_directory()`.

### Step 1 — Create the dataset class

Create `data/my_dataset.py`:

```python
from pathlib import Path
import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from .utils import set_seed

_DATA_ROOT = Path(__file__).parent.parent / "datasets"


class MyDataset(SandbagDataset):
    """Brief description of what the dataset measures."""

    SPLIT_NAMES = ("mos_train", "mos_eval", "elicit_train", "elicit_eval", "test")

    def __init__(self) -> None:
        super().__init__()

    def load(self, seed: int = 42) -> None:
        set_seed(seed)

        # Download from HuggingFace or load locally
        raw = load_dataset("your-hf-dataset-path")
        df = pd.DataFrame(raw["train"])

        # --- Required columns ---
        # "question_prompt"  : full formatted prompt string (str)
        # "answer"           : correct answer letter, e.g. "A" (str)

        # Split into standard proportions
        df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
        n = len(df)
        self.splits["mos_train"]    = df.iloc[:int(0.50 * n)].reset_index(drop=True)
        self.splits["mos_eval"]     = df.iloc[int(0.50 * n):int(0.60 * n)].reset_index(drop=True)
        self.splits["elicit_train"] = df.iloc[int(0.60 * n):int(0.85 * n)].reset_index(drop=True)
        self.splits["elicit_eval"]  = df.iloc[int(0.85 * n):int(0.90 * n)].reset_index(drop=True)
        self.splits["test"]         = df.iloc[int(0.90 * n):].reset_index(drop=True)
        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        self._require_loaded()
        if split not in self.splits:
            raise ValueError(f"Unknown split '{split}'. Choose from {self.SPLIT_NAMES}")
        return self.splits[split]

    def save(self, directory: str = None) -> None:
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        out_dir = self._ensure_dir(directory)
        filenames = {
            "mos_train":    "my_dataset_mos_train.csv",
            "mos_eval":     "my_dataset_mos_eval.csv",
            "elicit_train": "my_dataset_elicit_train.csv",
            "elicit_eval":  "my_dataset_elicit_eval.csv",
            "test":         "my_dataset_test.csv",
        }
        for split_name, filename in filenames.items():
            self.splits[split_name].to_csv(out_dir / filename, index=False)
        print(f"Saved MyDataset splits to {out_dir}/")

    @classmethod
    def from_directory(cls, directory: str = None) -> "MyDataset":
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")
        ds = cls.__new__(cls)
        super(MyDataset, ds).__init__()
        data_dir = Path(directory)
        filenames = {
            "mos_train":    "my_dataset_mos_train.csv",
            "mos_eval":     "my_dataset_mos_eval.csv",
            "elicit_train": "my_dataset_elicit_train.csv",
            "elicit_eval":  "my_dataset_elicit_eval.csv",
            "test":         "my_dataset_test.csv",
        }
        for split_name, filename in filenames.items():
            path = data_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"Expected {path}. Run MyDataset().load() and .save() first.")
            ds.splits[split_name] = pd.read_csv(path)
        ds._loaded = True
        return ds
```

**Required DataFrame columns:**

| Column | Type | Description |
|--------|------|-------------|
| `question_prompt` | str | Full prompt shown to the model, ending with e.g. `"\nAnswer:"` |
| `answer` | str | Ground-truth answer letter (`"A"`, `"B"`, `"C"`, or `"D"`) |

### Step 2 — Register in `data/__init__.py`

```python
from .base import SandbagDataset
from .wmdp import WMDPDataset
from .mmlu import MMLUDataset, MMLUProfDataset, MMLUStemDataset, MMLUHssDataset
from .my_dataset import MyDataset          # ← add this line
```

### Step 3 — Register in `evaluate.py`

**a. Add to `_VALID_DS`** (near the top of the file):
```python
_VALID_DS = {"wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "my_dataset"}
```

**b. Add a data-loading helper** (alongside `_load_wmdp_split()`):
```python
def _load_my_dataset_split(data_dir: str, model_name: str):
    """Load eval + elicit_train splits for MyDataset."""
    from possum.data.my_dataset import MyDataset
    ds = MyDataset.from_directory(f"{data_dir}/mcqa")
    eval_df = pd.concat([
        ds.get_split("mos_eval"),
        ds.get_split("elicit_eval"),
        ds.get_split("test"),
    ], ignore_index=True)
    elicit_train_df = ds.get_split("elicit_train")

    question_col = get_question_column(model_name)
    if question_col not in eval_df.columns:
        question_col = "question_prompt"
    # my_dataset has no password-locked variant, so question_col_pw == question_col
    return eval_df, elicit_train_df, question_col, question_col
```

**c. Add a loading branch in `_eval_single()`** (in the dataset loading section):
```python
elif dataset == "my_dataset":
    eval_df, elicit_train_df, question_col, question_col_pw = \
        _load_my_dataset_split(data_dir, model_name)
```

**d. Add to `--datasets` choices** in `parse_args()`:
```python
parser.add_argument("--datasets", nargs="+",
                    choices=["wmdp", "bio", "chem", "cyber", "my_dataset"])
```

### Step 4 — Register in `train.py`

**a. Add to `_ALL_DATASETS`** (near the top of the file):
```python
_ALL_DATASETS = {"wmdp", "bio", "chem", "cyber", "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss", "my_dataset"}
```

**b. Add to `--datasets` help text** in `parse_args()` so the choices validation covers it.

---

## 2. Adding a New Model Organism

### Overview

All model organisms inherit from `ModelOrganism` (`modelorgs/base.py`). The base class requires `train()` and `save()`, and enforces dataset compatibility via `SUPPORTED_DATASETS`.

### Checkpoint formats

Choose the format that matches your mechanism:

| Format | How to save | How `_load_mo_model()` loads it | Example |
|--------|------------|----------------------------------|---------|
| **Merged weights** | `model.merge_and_unload().save_pretrained(out)` | `load_model("local-checkpoint")` | `PasswordLock`, `RobustPasswordLock` |
| **Adapter only** | `peft_model.save_pretrained(out / "adapter")` + `config.json` with `adapter_subdirs` | Load base from HF, then `attach_adapter_to_model()` | `RobustAdaptor` |
| **Config only** | `config.json` with `system_prompt` key | Load base from HF, read system prompt from config | `SystemPrompt` |
| **Weights + config** | Both `save_pretrained(out)` and `config.json` with `system_prompt` | `load_model("local-checkpoint")` + read `config.json` | `RobustSystemPrompt` |

### Step 1 — Create the organism class

Create `modelorgs/my_organism.py`:

```python
from pathlib import Path
import torch
from .base import ModelOrganism
from ..data.utils import set_seed
from ..utils.column_utils import get_question_column
from ..utils.loading import load_model


class MyOrganism(ModelOrganism):
    """Brief description of the sandbagging mechanism."""

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber")

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        epochs: int = 3,
        lr: float = 5e-5,
        seed: int = 42,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.epochs = epochs
        self.lr = lr
        self.model = None
        self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"

        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,
        )

        question_column = get_question_column(self.model_name)

        import pandas as pd
        train_df = pd.read_csv(self.data_dir / "mcqa" / "wmdp_mos_train.csv")
        if question_column not in train_df.columns:
            question_column = "question_prompt"

        # --- YOUR TRAINING LOGIC HERE ---
        # Use train_model() from utils/training.py for standard SFT loops.
        # Use apply_short_circuiting() from modelorgs/_rr_internals.py for RR.
        # Use the LAT helpers from modelorgs/_lat_internals.py for LAT hardening.

        # Merge LoRA into base weights so the checkpoint is self-contained:
        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        if self.model is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(str(out))
        self.tokenizer.save_pretrained(str(out))
        print(f"Saved MyOrganism to {out}/")

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, model_name: str, dataset: str = "wmdp") -> "MyOrganism":
        device = "cuda" if torch.cuda.is_available() else "cpu"
        org = cls.__new__(cls)
        ModelOrganism.__init__(org, model_name=model_name, dataset=dataset)
        org.model, org.tokenizer = load_model(
            model_name=model_name,
            load_from="local-checkpoint",
            device=device,
            lora=False,
            model_path=checkpoint_path,
        )
        return org
```

**Multi-dataset support**: if the organism should work on multiple datasets simultaneously, add a `MultiDatasetMyOrganism` class following the pattern in `MultiDatasetRobustAdaptor` (`robust_adaptor.py`) or `MultiDatasetRobustPasswordLock` (`robust_password_lock.py`).

> **Important**: the multi-dataset class must **not** subclass `ModelOrganism` because `ModelOrganism.__init__` enforces single-dataset validation. Validate `datasets` manually in `__init__` (unknown names, duplicates, minimum count of 2). See `MultiDatasetRobustAdaptor.__init__` for the pattern.

**Compose, don't inherit**: when a new organism extends an existing one with an additional phase (e.g. adding LAT on top of distilled SFT), prefer **composition** over inheritance. `RobustCoT` owns a `self._distilled = DistilledCoT(...)` member and runs `self._distilled.train()` before running its own LAT phase. This keeps stage boundaries explicit, avoids fragile `super()` coupling, and makes it trivial to swap one stage without re-deriving class hierarchies. Follow this pattern whenever the new organism is "existing organism + extra phase".

**Key utilities available:**
- `utils/training.py`: `train_model()` — SFT loop with eval callbacks and gradient accumulation; `GenericTorchDataset`, `GenericPadCollate`, `get_n_correct_answers_for_dataloader`
- `modelorgs/_rr_internals.py`: `apply_short_circuiting()` — representation-rerouting loop (CircuitBreak)
- `modelorgs/_lat_internals.py`: `build_lat_dataloader()`, `_build_lat_items()` — LAT data preparation; re-exports `GDAdversary`, `do_adversary_step`, `do_defense_step` from `LAT/`

### Step 2 — Register in `modelorgs/__init__.py`

```python
from .my_organism import MyOrganism          # ← add this line

__all__ = [..., "MyOrganism"]
```

For multi-dataset:
```python
from .my_organism import MyOrganism, MultiDatasetMyOrganism

__all__ = [..., "MyOrganism", "MultiDatasetMyOrganism"]
```

### Step 3 — Register in `train.py`

**a. Add to `--model_org` choices** in `parse_args()` (~line 79):
```python
choices=["pwd", "circuitbreak", "sysprompt", "robpwd", "robsysprompt", "robadapt", "my_organism"]
```

**b. Add organism-specific CLI arguments** inside `parse_args()`, after the existing organism-specific blocks:
```python
# my_organism-specific
parser.add_argument("--my_param", type=float, default=1.0,
                    help="Example parameter for MyOrganism.")
```

**c. Add a dispatch branch in `_build_organism()`**:
```python
elif args.model_org == "my_organism":
    if multi:
        from possum.modelorgs import MultiDatasetMyOrganism
        return MultiDatasetMyOrganism(
            datasets=ds_arg,
            model_name=args.model,
            data_dir=args.data_dir,
            epochs=args.epochs,
            lr=args.lr,
            seed=args.seed,
            my_param=args.my_param,
        )
    else:
        from possum.modelorgs import MyOrganism
        return MyOrganism(
            dataset=ds_arg,
            model_name=args.model,
            data_dir=args.data_dir,
            epochs=args.epochs,
            lr=args.lr,
            seed=args.seed,
            my_param=args.my_param,
        )
```

The same `_build_organism()` function handles both single (`multi=False`) and multi (`multi=True`) modes, so no second script needs to be changed.

### Step 4 — Register in `evaluate.py`

**a. Add to `_VALID_MODEL_ORGS`** (~line 64):
```python
_VALID_MODEL_ORGS = {"pwd", "circuitbreak", "sysprompt", "robpwd", "robsysprompt", "robadapt", "my_organism"}
```

Two-word organism names (e.g. `"rob_something"`) are parsed by the `f"{parts[0]}_{parts[1]}"` branch in `_parse_model_path()` automatically — no extra path parsing code needed.

**b. Add a loading branch in `_load_mo_model()`**:

For **merged-weights** organisms (checkpoint contains full model weights):
```python
# pwd, circuitbreak, robpwd, my_organism all fall into the default else branch:
else:
    mo_model, tokenizer = load_model(
        model_name, load_from="local-checkpoint", device=device,
        lora=False, model_path=model_path,
    )
```
If your organism saves merged weights with no `config.json`, it will already work via the existing `else:` branch — no change needed.

For **system-prompt-style** organisms (need to read `config.json` for `system_prompt`):
```python
elif model_org == "my_organism":
    cfg = json.load(open(Path(model_path) / "config.json"))
    system_prompt = cfg["system_prompt"]
    mo_model, tokenizer = load_model(
        model_name, load_from="local-checkpoint", device=device,
        lora=False, model_path=model_path,
    )
```

For **adapter-only** organisms (no merged weights; adapter loaded separately):
```python
elif model_org == "my_organism":
    cfg = json.load(open(Path(model_path) / "config.json"))
    adapter_paths = {k: str(Path(model_path) / v) for k, v in cfg["adapter_subdirs"].items()}
    from possum.modelorgs._adapter_merging import attach_adapter_to_model
    base_model, tokenizer = load_model(model_name, load_from="hf", device=device, lora=False)
    mo_model = attach_adapter_to_model(base_model, adapter_paths, strategy=cfg["attachment_strategy"])
```

**c. Elicitation compatibility** — add to `_run_elicitations_single()`:

- For organisms that follow the **password-locking pattern** (elicited via `many_shot`, `prefilling`, `gcg`, `autodan`, `tap`): the default `else:` branch in `_run_elicitations_single()` already handles these — set `compat = _PWD_CB_TECHNIQUES` is the existing code path. No change needed if your organism falls into this category.
- For organisms that follow the **system-prompt pattern** (elicited via `prompt_injection`/jailbreaks), extend the condition:
  ```python
  if model_org in ("sysprompt", "robsysprompt", "my_organism"):
  ```
- For organisms with an **entirely new technique set**: define `_MY_TECHNIQUES = {"my_technique", ...}`, add it to `_ALL_TECHNIQUES`, and add a new branch in `_run_elicitations_single()`.

**d. Effectiveness display** — in `_eval_wmdp_mo()`, `robadapt` has special handling because it has no password trigger (returns `pw_correct=None`). If your organism similarly has no password-unlock mechanism, extend that condition:
```python
if model_org in ("robadapt", "my_organism"):
    return nopw_correct, None
```

---

## 3. Adding a New Elicitation Technique

### Overview

All elicitation techniques inherit from `ElicitationTechnique` (`elicitation/base.py`). They are either **blackbox** (query-only) or **whitebox** (require weights/gradients). `run()` receives eval and optional training data and returns a dict with at least `{"accuracy": float}`.

### Step 1 — Create the technique class

Place the file in `elicitation/blackbox/` or `elicitation/whitebox/` depending on type.

**Template: `elicitation/blackbox/my_technique.py`**

```python
"""One-line description of the technique."""

import torch
from torch.utils.data import DataLoader
import pandas as pd

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import GROUND_TRUTH_ANSWER_COLUMN, get_question_column
from ...utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    get_n_correct_answers_for_dataloader,
)


class MyTechnique(ElicitationTechnique):
    """Full description of the technique and when to use it."""

    SUPPORTED_DATASETS = ("wmdp",)
    elicitation_type = ElicitationType.BLACKBOX  # or WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        seed: int = 42,
        my_param: float = 1.0,
    ) -> None:
        super().__init__(model=model, tokenizer=tokenizer,
                         dataset=dataset, model_name=model_name, seed=seed)
        self.my_param = my_param

    def run(
        self,
        eval_data: pd.DataFrame,
        train_data: pd.DataFrame | None = None,
        **kwargs,
    ) -> dict[str, float]:
        set_seed(self.seed)
        device = next(self.model.parameters()).device
        ctx = torch.amp.autocast(device_type=str(device).split(":")[0], dtype=torch.bfloat16)

        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        # --- YOUR ELICITATION LOGIC HERE ---
        dataset = GenericTorchDataset(
            eval_data,
            prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        loader = DataLoader(
            dataset, batch_size=16, shuffle=False,
            collate_fn=GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True),
        )

        torch.set_grad_enabled(False)
        self.model.eval()
        n_correct = get_n_correct_answers_for_dataloader(self.model, self.tokenizer, loader, ctx)
        accuracy = n_correct / len(eval_data)

        return {"accuracy": accuracy, "n_correct": n_correct, "n_total": len(eval_data)}
```

For whitebox techniques that modify the model, change `elicitation_type = ElicitationType.WHITEBOX` and add gradient-based logic inside `run()`. See `elicitation/whitebox/sft.py` for a fine-tuning example and `elicitation/whitebox/steering.py` for an activation-steering example.

#### Step-tracking contract (required for training-based whitebox techniques)

Training-based elicitation techniques (sft, anti_refusal, lat, ppo, dpo, grpo) follow a shared **step-tracking contract** so that `evaluate.py` can print "best accuracy achieved at step N" and plot elicitation curves:

* Constructor accepts: `eval_every_steps: int = 500`, `max_steps: int | None = None`, `results_dir: str | None = None`.
* At every `eval_every_steps` optimiser updates, evaluate on `eval_data` via `get_n_correct_answers_for_dataloader(...)` and append `(step, accuracy)` to an in-memory trajectory list.
* After training, write two files to `results_dir` (default `"elicitation_results"`):
  * `{technique}_{model_name}_{dataset}_curve.csv` — columns `step, accuracy, n_correct, n_total`
  * `{technique}_{model_name}_{dataset}_summary.json` — `{best_accuracy, elicited_at_step, final_accuracy, trajectory}`
* `run()` must return a dict containing at minimum `{"accuracy": best, "best_accuracy": best, "elicited_at_step": step, "final_accuracy": final, "trajectory": [(step, acc), ...]}`. The `"accuracy"` key is the best-of-trajectory so `evaluate.py`'s existing display path "does the technique elicit, and when?" works unchanged.
* No plot is produced at runtime — plots are off-line from the CSV trajectory.

Step-tracked names also belong in `_STEP_TRACKED_TECHNIQUES` (evaluate.py) so `_print_elicit_results()` renders `best = XX.X% (at step N, final = YY.Y%)`.

#### Combining two related techniques under one class (Steering + MELBO pattern)

`elicitation/whitebox/steering.py` contains a single `Steering` class that runs both a supervised contrast-pair steering pass and an unsupervised MELBO pass, returning `{"accuracy": max(steering, melbo), "accuracy_steering": ..., "accuracy_melbo": ...}`. `evaluate.py` detects these sub-accuracy keys and prints both sub-entries. Prefer this pattern over two parallel classes when the sub-techniques share setup (same LoRA config, same eval harness) but differ only in optimisation objective.

#### Whitebox fine-tuning elicitations share a common HHH safety source

`sft`, `anti_refusal`, `dpo`, `ppo`, and `grpo` all draw their training
corpus from `Anthropic/hh-rlhf` via `elicitation/whitebox/_hhh_data.py`.
Each technique thus becomes a *generic safety-tuning intervention* —
evaluation still runs on the organism's MCQA eval split, and the
elicitation question becomes "does sandbagging survive this intervention?".

`_hhh_data.py` exposes three loaders and one heuristic:

* `load_hhh_sft(subset_size, seed, tokenizer, max_length)` → `list[{"prompt", "completion"}]` (used by SFT).
* `load_hhh_pairs(subset_size, seed, tokenizer, max_length)` → `list[{"prompt", "chosen", "rejected"}]` (used by DPO).
* `load_hhh_prompts(subset_size, seed)` → `list[str]` (used by AntiRefusal, PPO, GRPO).
* `is_refusal(text) -> bool` — refusal-detection heuristic used as the PPO/GRPO reward.

Every HHH-using technique accepts three kwargs with identical defaults
(`hhh_subset_size=10000`, `hhh_seed=42`, `hhh_max_length=1024`). These are
resolvable through `HYPERPARAMETERS.toml` under `[defaults]` or any more
specific scope. The `train_data` kwarg is retained in each class's `run()`
signature but ignored — this keeps `_make_technique()`'s dispatch uniform
across all whitebox techniques.

**LAT is explicitly unchanged** — it operates on organism-local hidden-state
perturbations and does not use this module. To plug in a different HHH
source, replace the dataset name inside `_hhh_data.py` (or add a parallel
`_<source>_data.py`) and keep the three-loader contract.

### Step 2 — Register in the `__init__.py`

**Blackbox** — `elicitation/blackbox/__init__.py`:
```python
from .my_technique import MyTechnique          # ← add this line
__all__ = [..., "MyTechnique"]
```

**Whitebox** — `elicitation/whitebox/__init__.py`:
```python
from .my_technique import MyTechnique          # ← add this line
__all__ = [..., "MyTechnique"]
```

### Step 3 — Register in `evaluate.py`

**a. Add to the appropriate technique set** (~line 60):

```python
# For a blackbox WMDP technique (many_shot, prefilling, gcg, autodan, tap pattern):
_PWD_CB_TECHNIQUES = {"many_shot", "prefilling", "gcg", "autodan", "tap", "my_technique"}

# For a whitebox technique used by CoT organisms (sft, steering, anti_refusal, lat, ppo, dpo, grpo, noise pattern):
_WHITEBOX_TECHNIQUES = {"sft", "steering", "anti_refusal", "lat", "ppo", "dpo", "grpo", "noise", "my_technique"}

# For a system-prompt-compatible technique (prompt_injection pattern):
_SYSTEM_PROMPT_TECHNIQUES = {"prompt_injection", "gcg", "autodan", "tap", "my_technique"}
```

Adding to either set automatically includes the technique in `_ALL_TECHNIQUES` (the valid `--elicitations` choices) and in the compatibility routing in `_run_elicitations_single()`. `_COT_TECHNIQUES = _PWD_CB_TECHNIQUES | _WHITEBOX_TECHNIQUES` — both sets flow into it automatically.

> **Important**: the `_make_technique()` dispatch and the technique set membership are **independent**. A technique is only runnable if BOTH are wired — add a new `elif name == "my_technique":` branch in `_make_technique()` even if you've already added the name to `_WHITEBOX_TECHNIQUES`.

**b. Add a dispatch case to `_make_technique()`**:
```python
elif name == "my_technique":
    from possum.elicitation.blackbox.my_technique import MyTechnique
    return MyTechnique(
        model=model, tokenizer=tokenizer,
        dataset=dataset, model_name=model_name,
        seed=seed,
    )
```

**c. Add any technique-specific CLI arguments** in `parse_args()` (after the `--seed` argument):
```python
parser.add_argument("--my_param", type=float, default=1.0,
                    help="Example param for MyTechnique.")
```

**Note on `jailbreak` shorthand**: `jailbreak` is a CLI alias that expands to `gcg autodan tap` inside `main()`. If you want `my_technique` included in `jailbreak`, add it to the expansion there.

---

## Summary of files to modify

| What you're adding | New file | Files to modify |
|--------------------|----------|-----------------|
| Dataset | `data/my_dataset.py` | `data/__init__.py`, `evaluate.py` (`_VALID_DS` + loader helper + `_eval_single` branch + `--datasets` choices), `train.py` (`_ALL_DATASETS` + `--datasets` choices) |
| Model organism | `modelorgs/my_organism.py` | `modelorgs/__init__.py`, `train.py` (`--model_org` choices + args + `_build_organism()` branch), `evaluate.py` (`_VALID_MODEL_ORGS` + `_load_mo_model()` branch + elicitation routing + effectiveness display) |
| Multi-dataset organism | `modelorgs/my_organism.py` (add `MultiDatasetMyOrganism`) | `train.py` (`_build_organism()` `multi=True` branch) — no second CLI needed |
| Elicitation technique | `elicitation/{blackbox\|whitebox}/my_technique.py` | `elicitation/{blackbox\|whitebox}/__init__.py`, `evaluate.py` (technique set + `_make_technique()` dispatch + optional CLI args) |

### Checklist for a complete model organism registration

- [ ] `modelorgs/my_organism.py` — class with `SUPPORTED_DATASETS`, `train()`, `save()`, optional `from_checkpoint()`
- [ ] `modelorgs/__init__.py` — import + `__all__` entry
- [ ] `train.py` — `--model_org` choice + organism-specific args + `elif` branch in `_build_organism()`
- [ ] `evaluate.py` — `_VALID_MODEL_ORGS` + `_load_mo_model()` branch (if not using default merged-weights path) + elicitation routing + effectiveness display
- [ ] `requirements.txt` — add any new dependencies
- [ ] `COMMANDS_*.md` — document training + evaluation commands
