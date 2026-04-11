# Extending SandbagBench

This guide explains how to add new **datasets**, **model organism training algorithms**, and **elicitation techniques** to the framework. Each section lists every file that must be modified and provides a complete code template.

---

## 1. Adding a New Dataset

### Overview

All datasets inherit from `SandbagDataset` (`data/base.py`). The base class enforces a standard interface: `load()`, `get_split()`, `save()`, and optionally `from_directory()`.

### Step 1 — Create the dataset class

Create `SandbagBench/data/my_dataset.py`:

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

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        set_seed(seed)

        # Download from HuggingFace or load locally
        raw = load_dataset("your-hf-dataset-path")
        df = pd.DataFrame(raw["train"])

        # --- Required columns ---
        # "question_prompt"  : full formatted prompt string (str)
        # "answer"           : correct answer letter, e.g. "A" (str)
        # Optional MCQA columns: "question", "choices", "subject"

        # Split into standard proportions (adjust sizes as needed)
        df = df.sample(frac=1.0, random_state=seed).reset_index(drop=True)
        n = len(df)
        mos_train    = df.iloc[:int(0.50 * n)]
        mos_eval     = df.iloc[int(0.50 * n):int(0.60 * n)]
        elicit_train = df.iloc[int(0.60 * n):int(0.85 * n)]
        elicit_eval  = df.iloc[int(0.85 * n):int(0.90 * n)]
        test         = df.iloc[int(0.90 * n):]

        self.splits["mos_train"]    = mos_train.reset_index(drop=True)
        self.splits["mos_eval"]     = mos_eval.reset_index(drop=True)
        self.splits["elicit_train"] = elicit_train.reset_index(drop=True)
        self.splits["elicit_eval"]  = elicit_eval.reset_index(drop=True)
        self.splits["test"]         = test.reset_index(drop=True)
        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        self._require_loaded()
        if split not in self.splits:
            raise ValueError(f"Unknown split '{split}'. Choose from {self.SPLIT_NAMES}")
        return self.splits[split]

    def save(self, directory: str = None) -> None:
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "mcqa")   # or "code" for code-gen datasets
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
# data/__init__.py
from .base import SandbagDataset
from .wmdp import WMDPDataset
from .mmlu import MMLUDataset
from .my_dataset import MyDataset          # ← add this line
```

### Step 3 — Register in the evaluation CLI (`evaluation.py`)

In the `if __name__ == "__main__"` block:

**a. Import the dataset** (alongside existing imports ~line 243):
```python
from sandbagBench.data.my_dataset import MyDataset
```

**b. Add to the `--dataset` choices** (~line 265):
```python
parser.add_argument("--dataset", choices=["wmdp", "bio", "chem", "cyber", "my_dataset"], required=True)
```

**c. Add to `DATASET_ELICITATION_COMPAT`** (~line 248):
```python
DATASET_ELICITATION_COMPAT = {
    ...
    "my_dataset": {"none", "prefilling", "sft"},   # list compatible elicitation methods
}
```

**d. Add a loading branch** (in the dataset loading section of `evaluate.py`):
```python
elif args.dataset == "my_dataset":
    ds = MyDataset.from_directory(f"{data_dir}/mcqa")
    eval_df = ds.get_split("test")
    elicit_train_df = ds.get_split("elicit_train")
```

### Step 4 — Register in the training CLI (`train_modorg.py`)

Add `"my_dataset"` to the `--dataset` choices (~line 41):
```python
parser.add_argument("--dataset", required=True, choices=["wmdp", "bio", "chem", "cyber", "my_dataset"])
```

---

## 2. Adding a New Model Organism

### Overview

All model organisms inherit from `ModelOrganism` (`modelorgs/base.py`). The base class requires `train()` and `save()`, and enforces dataset compatibility via `SUPPORTED_DATASETS`.

### Checkpoint formats

There are three checkpoint formats used by existing organisms. Choose the one that matches your mechanism:

| Format | How to save | How to load in `evaluate.py` | Example |
|--------|------------|-------------------------------|---------|
| **Merged weights** | `model.merge_and_unload().save_pretrained(out)` | `load_model("local-checkpoint")` | `PasswordLock`, `RobustPasswordLock` |
| **Adapter only** | `peft_model.save_pretrained(out / "adapter")` + `config.json` with `adapter_subdirs` | Load base model from HF, then `attach_adapter_to_model()` | `RobustAdaptor` |
| **Config only** | Only `config.json` with `system_prompt` string | Load base model from HF, read system prompt from config | `SystemPrompt` |

For **adapter-only** organisms: `save()` must write a `config.json` that includes `"adapter_subdirs": {dataset: subdir}` so `evaluate.py` knows where to find the adapter weights. See `modelorgs/robust_adaptor.py` for a complete example.

### Step 1 — Create the organism class

Create `SandbagBench/modelorgs/my_organism.py`:

```python
from pathlib import Path
import torch
from .base import ModelOrganism
from ..data.utils import set_seed
from ..utils.column_utils import BASE_QUESTION_COLUMN, get_question_column
from ..utils.loading import load_model


class MyOrganism(ModelOrganism):
    """Brief description of the sandbagging mechanism."""

    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber")   # declare which datasets are supported

    def __init__(
        self,
        model_name: str,
        dataset: str,
        data_dir: str = None,
        epochs: int = 3,
        lr: float = 5e-5,
        seed: int = 42,
        wandb_project: str | None = None,
    ) -> None:
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "datasets")
        self.data_dir = Path(data_dir)
        self.epochs = epochs
        self.lr = lr
        self.wandb_project = wandb_project
        self.model = None
        self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        device = "cuda" if torch.cuda.is_available() else "cpu"

        self.model, self.tokenizer = load_model(
            model_name=self.model_name,
            load_from="hf",
            device=device,
            lora=True,          # set False if not using LoRA
        )

        # Determine which question column to use; fall back to base column
        # if the model-specific chat-template column isn't present in the CSV.
        question_column = get_question_column(self.model_name)

        # Load training data
        import pandas as pd
        train_df = pd.read_csv(self.data_dir / "mcqa" / "wmdp_mos_train.csv")

        if question_column not in train_df.columns:
            question_column = BASE_QUESTION_COLUMN

        # --- YOUR TRAINING LOGIC HERE ---
        # Use train_model() from utils/training.py for standard SFT loops,
        # or apply_short_circuiting() from modelorgs/_rr_internals.py for
        # representation-rerouting approaches.

        # After LoRA training, merge weights so the checkpoint is self-contained:
        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        if self.model is None or self.tokenizer is None:
            raise RuntimeError("Must call train() before save().")
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        # Always save merged weights (not raw LoRA adapter) for compatibility with
        # load_model("local-checkpoint") and the CircuitBreak organism.
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

**Key utilities available in `utils/training.py`:**
- `train_model(model, train_loader, optimizer, ctx, ...)` — generic SFT training loop with eval callbacks and gradient accumulation
- `GenericTorchDataset` / `GenericPadCollate` — dataset and collate utilities
- `get_n_correct_answers_for_dataloader` — MCQA accuracy evaluation

**Key utilities in `modelorgs/_rr_internals.py`:**
- `apply_short_circuiting(...)` — representation-rerouting training loop (used by CircuitBreak)

### Step 2 — Register in `modelorgs/__init__.py`

```python
from .base import ModelOrganism
from .password_lock import PasswordLock
from .circuit_break import CircuitBreak
from .system_prompt import SystemPrompt
from .my_organism import MyOrganism          # ← add this line

__all__ = ["ModelOrganism", "PasswordLock", "CircuitBreak", "SystemPrompt", "MyOrganism"]
```

### Step 3 — Register in the training CLI (`train_modorg.py`)

**(a) Add to `--model_org` choices** (~line 30):
```python
parser.add_argument("--model_org", required=True,
                    choices=["pwd", "circuitbreak", "system_prompt", "my_organism"])
```

**(b) Add organism-specific CLI arguments** after the existing LAT/robpwd args near the
bottom of `parse_args()`:
```python
# MyOrganism-specific
parser.add_argument("--my_param", type=float, default=1.0,
                    help="Example param for MyOrganism.")
```

**(c) Add a training branch** in `main()` (after the `robsysprompt` block, before
`organism.train()`):
```python
elif args.model_org == "my_organism":
    from sandbagBench.modelorgs import MyOrganism
    organism = MyOrganism(
        model_name=args.model,
        dataset=args.dataset,
        data_dir=args.data_dir,
        epochs=args.epochs,
        lr=args.lr,
        seed=args.seed,
        wandb_project=args.wandb_project,
    )
```

**(d) Multi-dataset support** — if the organism should be available in
`train_modorg_scale.py`, apply the same (a)/(b)/(c) changes there too.
The scale CLI always instantiates a *multi-dataset* variant of the organism
(e.g. `MultiDatasetMyOrganism`). Create this class following the pattern in
`MultiDatasetRobustAdaptor` (in `robust_adaptor.py`) or
`MultiDatasetRobustPasswordLock` (in `robust_password_lock.py`).

> **Important**: the multi-dataset class must **NOT** subclass `ModelOrganism`
> because `ModelOrganism.__init__` enforces single-dataset validation. Instead,
> validate `datasets` manually in `__init__` (check for unknown names, duplicates,
> and minimum count of 2). See `MultiDatasetRobustAdaptor.__init__` for the
> validation pattern.

### Step 3d — Register in `_parse_model_path()` (both `evaluate.py` and `eval_scale.py`)

Inside `_parse_model_path()` in **both** `evaluate.py` and `eval_scale.py`, find the local
`_VALID_MODEL_ORGS` set and add the new organism name:

```python
_VALID_MODEL_ORGS = {"pwd", "circuitbreak", "system_prompt", "robpwd", "my_organism"}
```

- **Single-token names** (e.g. `"robpwd"`, `"robadapt"`) are matched by the
  `elif parts[0] in _VALID_MODEL_ORGS:` branch automatically.
- **Two-token names** (e.g. `"system_prompt"`) need the
  `f"{parts[0]}_{parts[1]}"` prefix check that already exists in the parser.

Without this registration, passing only `--model_path` will raise a `ValueError` because
the organism name cannot be inferred from the checkpoint directory name.

### Step 4 — Register in the evaluation CLIs (`evaluate.py` and `eval_scale.py`)

Apply the changes below to **both files**.

**a. Add to `--model_org` choices** (in the `argparse` block):
```python
parser.add_argument("--model_org", required=True,
                    choices=["pwd", "circuitbreak", "system_prompt", "my_organism"])
```

**b. Elicitation compatibility**:
- For organisms that follow the **password-locking pattern**: no additional registration
  needed — they automatically inherit `_WMDP_BLACKBOX`/`_WMDP_WHITEBOX` technique sets.
- For organisms that follow the **system-prompt pattern** (inference-time system prompt,
  elicited via `prompt_injection`): change the compatibility validation condition from
  `if args.model_org == "system_prompt":` to
  `if args.model_org in ("system_prompt", "my_organism"):` in both `evaluate.py` and
  `eval_scale.py`. Also update `_compatible_techniques()` in `eval_scale.py` the same way.
- For organisms with an **entirely new technique set**: define a new `_MY_TECHNIQUES` set,
  add it to `_ALL_TECHNIQUES`, and add a new branch in the validation block in `main()`.

**c. Add a loading branch** (in the `else:` block that handles `pwd` and `circuitbreak`,
or as a separate `elif`):

For **password-locking-style organisms** (checkpoint contains merged weights, no system prompt):
```python
elif args.model_org == "my_organism":
    mo_model, tokenizer = load_model(
        args.model, load_from="local-checkpoint", device=device,
        lora=False, model_path=args.model_path,
    )
    mo_model.eval()
    system_prompt = None
```

For **hybrid organisms** (trained weights AND a system prompt used at inference time):
```python
elif args.model_org == "my_organism":
    # 1. Load trained weights from checkpoint (not from HuggingFace)
    mo_model, tokenizer = load_model(
        args.model, load_from="local-checkpoint", device=device,
        lora=False, model_path=args.model_path,
    )
    # 2. Read system prompt from config.json
    import json as _json
    _cfg = _json.load(open(Path(args.model_path) / "config.json"))
    system_prompt = _cfg["system_prompt"]
    mo_model.eval()
    bias_dict = get_abcd_bias(tokenizer, args.model)
    # 3. Evaluate using chat-template collation with the system prompt
    sp_collate = GenericPadCollate(tokenizer, add_bos=False, add_eos=False,
                                   chat_template=True, system_inst=system_prompt)
    # ... (build loaders, run get_n_correct_answers_for_dataloader, set mo_pw_correct = None)
```
Also extend all `if args.model_org == "system_prompt":` display conditions to
`if args.model_org in ("system_prompt", "my_organism"):` (effectiveness labels and
robustness section header).

Password-locked and circuit-broken organisms (including `robpwd`) load identically via
`load_model(..., load_from="local-checkpoint")` — there is no organism-specific loading
logic needed if the checkpoint contains merged weights.

**d. Hybrid save format** — if your organism both trains model weights *and* needs a system
prompt at inference time, `save()` must write both:
1. `self.model.merge_and_unload().save_pretrained(str(out))` and
   `self.tokenizer.save_pretrained(str(out))` — for checkpoint loading.
2. A `config.json` in the same directory containing at minimum
   `{"organism_type": "...", "model_name": ..., "system_prompt": ...}` — for reconstructing
   the system prompt at inference time.

`from_checkpoint()` must correspondingly call both `load_model(..., load_from="local-checkpoint")`
and `json.load(open(config_path))` to recover `system_prompt`.

---

## 3. Adding a New Elicitation Technique

### Overview

All elicitation techniques inherit from `ElicitationTechnique` (`elicitation/base.py`). They are either **blackbox** (query only, no weights needed) or **whitebox** (require model weights or gradients). The `run()` method receives eval and optional training data and returns a dict with at least `{"accuracy": float}`.

### Step 1 — Create the technique class

Place the file in `elicitation/blackbox/` or `elicitation/whitebox/` depending on type.

**Template: `SandbagBench/elicitation/blackbox/my_technique.py`**

```python
"""One-line description of the technique."""

import torch
from torch.utils.data import DataLoader
import pandas as pd

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import (
    GROUND_TRUTH_ANSWER_COLUMN,
    get_question_column,
    get_correctness_column,
    get_model_answer_column,
)
from ...utils.training import (
    GenericTorchDataset,
    GenericPadCollate,
    get_n_correct_answers_for_dataloader,
)


class MyTechnique(ElicitationTechnique):
    """Full description of the technique and when to use it."""

    SUPPORTED_DATASETS = ("wmdp",)              # declare dataset compatibility
    elicitation_type = ElicitationType.BLACKBOX  # or WHITEBOX

    def __init__(
        self,
        model,
        tokenizer,
        dataset: str,
        model_name: str,
        seed: int = 42,
        # Add technique-specific hyperparameters here:
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
        """Run the elicitation technique and return accuracy metrics.

        Args:
            eval_data:  Test DataFrame (required).
            train_data: Training DataFrame (optional; needed for whitebox methods).

        Returns:
            dict with at least {"accuracy": float}.
        """
        set_seed(self.seed)
        device = next(self.model.parameters()).device
        ctx = torch.amp.autocast(device_type=str(device).split(":")[0], dtype=torch.bfloat16)

        # Always fall back to the base "question_prompt" column if the
        # model-specific chat-template column is absent from the CSV.
        question_column = get_question_column(self.model_name)
        if question_column not in eval_data.columns:
            question_column = "question_prompt"

        correctness_column = get_correctness_column(self.model_name)

        # --- YOUR ELICITATION LOGIC HERE ---
        # Example: evaluate the model as-is (equivalent to --elicitation none)
        eval_data = eval_data.copy()
        dataset = GenericTorchDataset(
            eval_data,
            prompt_column=question_column,
            completion_column=GROUND_TRUTH_ANSWER_COLUMN,
        )
        loader = DataLoader(
            dataset,
            batch_size=16,
            shuffle=False,
            collate_fn=GenericPadCollate(self.tokenizer, add_bos=False, add_eos=True),
        )

        torch.set_grad_enabled(False)
        self.model.eval()
        n_correct = get_n_correct_answers_for_dataloader(self.model, self.tokenizer, loader, ctx)
        accuracy = n_correct / len(eval_data)

        return {"accuracy": accuracy, "n_correct": n_correct, "n_total": len(eval_data)}
```

**For whitebox techniques** that modify the model, change `elicitation_type` and add gradient-based logic inside `run()`. See `elicitation/whitebox/sft.py` for a fine-tuning example and `elicitation/whitebox/steering.py` for an activation-steering example.

### Step 2 — Register in the `__init__.py`

**For blackbox:** `elicitation/blackbox/__init__.py`
```python
from .many_shot import ManyShot
from .prefilling import Prefilling
from .prompt_injection import PromptInjection
from .my_technique import MyTechnique          # ← add this line

__all__ = ["ManyShot", "Prefilling", "PromptInjection", "MyTechnique"]
```

**For whitebox:** `elicitation/whitebox/__init__.py`
```python
from .gcg import GCG
from .sft import SFT
from .steering import SteeringVector
from .melbo import MELBO
from .anti_refusal import AntiRefusal
from .my_technique import MyTechnique          # ← add this line

__all__ = ["GCG", "SFT", "SteeringVector", "MELBO", "AntiRefusal", "MyTechnique"]
```

### Step 3 — Register in the evaluation CLIs (`evaluate.py` and `eval_scale.py`)

Both `evaluate.py` and `eval_scale.py` share the same registration pattern. Apply the changes below to **both files**.

**a. Add to the appropriate technique set** (near the top of the file, after the `_WMDP_BLACKBOX` / `_WMDP_WHITEBOX` declarations):

- For a **blackbox WMDP** technique, add its name to `_WMDP_BLACKBOX`:
  ```python
  _WMDP_BLACKBOX = {"many_shot", "prefilling", "autodan", "tap", "my_technique"}
  ```
- For a **whitebox WMDP** technique, add its name to `_WMDP_WHITEBOX`:
  ```python
  _WMDP_WHITEBOX = {"gcg", "sft", "steering", "melbo", "anti_refusal", "my_technique"}
  ```
Adding to these sets automatically includes the technique in `_ALL_TECHNIQUES` (used for `--elicitations` choices) and in the compatibility validation logic.

**b. Add a dispatch case to `_make_technique()`** (in both files, the function starts around line 92):
```python
elif name == "my_technique":
    from sandbagBench.elicitation.blackbox.my_technique import MyTechnique
    return MyTechnique(
        model=model, tokenizer=tokenizer,
        dataset=dataset, model_name=model_name,
        seed=seed,
    )
```

**c. Add any technique-specific CLI arguments** (after the existing `--seed` argument):
```python
parser.add_argument("--my_param", type=float, default=1.0,
                    help="Example param for MyTechnique.")
```

Note: `evaluate.py` uses `--elicitations` (plural, `nargs="+"`) — you can pass multiple technique names at once. The special value `jailbreak` expands to `gcg autodan tap`. No `eval_df.to_csv()` call is needed; results are printed to stdout.

**Special case: `prompt_injection`** automatically runs three additional jailbreak-based injections (GCG, AutoDAN, TAP) whenever `train_data` is provided to `tech.run()`. These test whether adversarial content in the user turn overrides the sandbagging system prompt (*indirect prompt injection*). Both `evaluate.py` and `eval_scale.py` pass `train_data` to `prompt_injection.run()` unconditionally, so jailbreak injections are always included in system-prompt organism evaluations. Results appear in the output under "Jailbreak injection suffixes:" alongside the static injection strings.

---

## Summary of files to modify

| What you're adding | New file | Files to modify |
|--------------------|----------|-----------------|
| Dataset | `data/my_dataset.py` | `data/__init__.py`, `evaluate.py` (choices + loading branch), `train_modorg.py` (choices) |
| Model organism (single dataset) | `modelorgs/my_organism.py` | `modelorgs/__init__.py`, `train_modorg.py` (choices + args + branch), `evaluate.py` and `eval_scale.py` (choices + `_VALID_MODEL_ORGS` in `_parse_model_path()` + loading branch + display labels) |
| Model organism (multi-dataset support) | `modelorgs/my_organism.py` (add `MultiDataset` class) | `train_modorg_scale.py` (choices + args + branch), `eval_scale.py` (same changes as `evaluate.py`) |
| Elicitation technique | `elicitation/{blackbox\|whitebox}/my_technique.py` | `elicitation/{blackbox\|whitebox}/__init__.py`, `evaluate.py` **and** `eval_scale.py` (add to technique set + dispatch case in `_make_technique()` + optional CLI args) |

### Checklist for a complete model organism registration

- [ ] `modelorgs/my_organism.py` — class with `SUPPORTED_DATASETS`, `train()`, `save()`, optional `from_checkpoint()`
- [ ] `modelorgs/__init__.py` — import + `__all__` entry
- [ ] `train_modorg.py` — `--model_org` choice + any new args + `elif` branch in `main()`
- [ ] `train_modorg_scale.py` — same as above, using the `MultiDataset` variant
- [ ] `evaluate.py` — `--model_org` choice + `_VALID_MODEL_ORGS` + loading branch + display labels
- [ ] `eval_scale.py` — same as `evaluate.py`
- [ ] `requirements.txt` — add any new dependencies
- [ ] `COMMANDS_*.md` — document training + evaluation commands (optional but recommended)
