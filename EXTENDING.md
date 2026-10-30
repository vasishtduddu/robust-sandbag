# Extending the code

This guide covers the main extension points: a sandbagging **dataset** (task), a **model
organism**, an **elicitation technique**, and a **base model**. Each section gives a short
template and the registries to update. Symbols are referenced by name (search for them). Some
registries are deliberately duplicated so that the command-line scripts and the experiment tools
stay importable without torch, so update every place listed.

Add new third-party dependencies to `requirements.txt`.

## 1. Adding a dataset

### The five-split contract

Every task is a `SandbagDataset` (`data/base.py`) with the five splits in
`SandbagDataset.SPLIT_NAMES` (see the README for why they are kept apart):

| Split | Share | Role |
|---|---|---|
| `mos_train` | 45% | The only split a model organism trains on. |
| `mos_eval` + `elicit_eval` | 7% + 3% | Pooled into one validation set by `evaluate.py:_load_task_split`; used for tuning and seed/checkpoint selection. |
| `elicit_train` | 25% | The only split elicitation techniques fit on (passed to `run()` as `train_data`). |
| `test` | 20% | Held out for the reported numbers. Never used for training, tuning or selection. |

The fractions are the `*_FRAC` constants in `data/wmdp.py`. Use `data/wmdp.py:create_split(df, seed)`,
which returns the five frames in `SPLIT_NAMES` order, rather than splitting by hand.

Each split is a DataFrame with at least:

* `question_prompt` (`utils/column_utils.py:BASE_QUESTION_COLUMN`): the full prompt, ending in
  `Answer:`. The password is inserted before that cue by `data/utils.py:add_password_before_answer`.
* `answer` (`GROUND_TRUTH_ANSWER_COLUMN`): an option letter for multiple choice. Free-form answers
  follow the MATH path, which scoring detects by answer shape (`data/math.py:is_math_answer`).
* `subject`, as WMDP, MMLU and MATH provide.

For four-option sources with `subject`, `question`, `choices` and an integer `answer`,
`data/wmdp.py:preprocess_data` builds `question_prompt` and `answer`. The forced-choice readout
assumes options A to D.

### Step 1: the dataset class

`data/math.py:MATHDataset` is the most compact reference. A minimal version:

```python
# data/mytask.py
import csv
from pathlib import Path

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from .utils import set_seed
from .wmdp import create_split, preprocess_data

_DATA_ROOT = Path(__file__).parent.parent / "datasets"


class MyTaskDataset(SandbagDataset):
    _CSV_PREFIX = "mytask"

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        set_seed(seed)
        df = preprocess_data(pd.DataFrame(load_dataset("org/mytask")["test"]))
        for name, part in zip(self.SPLIT_NAMES, create_split(df, seed=seed)):
            self.splits[name] = part.reset_index(drop=True)
        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        self._require_loaded()
        return self.splits[split]

    def save(self, directory: str | None = None) -> None:
        self._require_loaded()
        out = self._ensure_dir(directory or str(_DATA_ROOT / "mcqa"))
        for name in self.SPLIT_NAMES:
            self.splits[name].to_csv(out / f"{self._CSV_PREFIX}_{name}.csv",
                                     index=False, quoting=csv.QUOTE_ALL)

    @classmethod
    def from_directory(cls, directory: str | None = None) -> "MyTaskDataset":
        data_dir = Path(directory or str(_DATA_ROOT / "mcqa"))
        paths = {s: data_dir / f"{cls._CSV_PREFIX}_{s}.csv" for s in cls.SPLIT_NAMES}
        if not all(p.exists() for p in paths.values()):   # download on first use
            fresh = cls()
            fresh.load()
            fresh.save(str(data_dir))
        ds = cls()
        ds.splits = {s: pd.read_csv(p) for s, p in paths.items()}
        ds._loaded = True
        return ds
```

### Step 2: register it

| File | What to add |
|---|---|
| `data/__init__.py` | Export the class. |
| `train.py` | The id in `_ALL_DATASETS` and `_VALID_MULTI_DATASETS`, and in the `--datasets` help text. |
| `evaluate.py` | The id in `_VALID_DS` (checkpoint-path parsing and `--datasets` help) and `_DS_LABEL` (display), and an explicit branch in `_load_task_split`. An unrecognised id falls through to the WMDP branch there, so the branch is required. |
| Each organism that should support it | The id in `SUPPORTED_DATASETS` (also in its `MultiDataset*` class), plus a branch in its data loader: `_load_wmdp_data` in `password_lock.py`, `robust_password_lock.py`, `robust_adaptor.py` and `robust_system_prompt.py`; `_load_data` in `circuit_break.py`; `_load_sandbagging_eval` in `distilled_cot.py`. Training reads `mos_train` only; a frame used for in-training monitoring must not drive checkpoint selection. |
| CoT organisms (`distcot`, `distcot_lat`, `obfcot`) | `_SUPPORTED` and `_load_mos_train` in `organisms/_cot_data_gen.py`, and the `--dataset` choices in `scripts/generate_cot_data.py`. |
| Each elicitation technique that should support it | The id in the technique's `SUPPORTED_DATASETS`. |
| `experiments/naming.py` | A label in `QWEN_TASKS` or `FAMILY_TASKS` (these feed `TASKS` and `TABLE_TASKS`). |
| `experiments/recipes.py` | Task-specific environment settings in `recipe_env` or `_family_env`, if any (for example the evaluation caps used for MATH). |

## 2. Adding a model organism

### Step 1: the organism class

Subclass `organisms/base.py:ModelOrganism`, set `SUPPORTED_DATASETS`, and implement `train()`
and `save()`. `ModelOrganism.__init__` rejects unsupported datasets.

```python
# organisms/my_organism.py
from pathlib import Path

from .base import ModelOrganism
from ..data.utils import set_seed
from ..utils.loading import load_model


class MyOrganism(ModelOrganism):
    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber",
                          "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")

    def __init__(self, model_name, dataset, data_dir, lr=5e-5, seed=42, my_param=1.0, **kw):
        super().__init__(model_name=model_name, dataset=dataset, seed=seed)
        self.data_dir, self.lr, self.my_param = Path(data_dir), lr, my_param
        self.model = self.tokenizer = None

    def train(self) -> None:
        set_seed(self.seed)
        self.model, self.tokenizer = load_model(self.model_name, load_from="hf", lora=True)
        # Train on mos_train only (see PasswordLock._load_wmdp_data for the CSV layout).
        ...
        self.model = self.model.merge_and_unload()

    def save(self, output_path: str) -> None:
        out = Path(output_path)
        out.mkdir(parents=True, exist_ok=True)
        self.model.save_pretrained(out)
        self.tokenizer.save_pretrained(out)
```

Reusable pieces: `utils/training.py` (`train_model`, `GenericTorchDataset`, `GenericPadCollate`),
`data/utils.py` (`modify_dataset_for_sandbagging`, `add_column_with_password`,
`sample_locked_answer`), `organisms/_lat_internals.py:build_lat_dataloader` (LAT),
`organisms/_rr_internals.py:apply_short_circuiting` (circuit breaking) and
`organisms/_oat_internals.py` (OAT probes). When a new organism is an existing one plus an extra
stage, compose rather than inherit: `DistilledCoTLAT` holds `self._distilled = DistilledCoT(...)`,
trains it, then runs its own stage.

For multi-dataset training add a `MultiDatasetMyOrganism` next to it. The existing `MultiDataset*`
classes do not subclass `ModelOrganism` (whose constructor takes one dataset) and validate their
`datasets` list themselves; follow `MultiDatasetPasswordLock`.

### Checkpoint formats

`evaluate.py:_load_mo_model` (single dataset) and the matching block in `_eval_multi` load the
organism. Use one of the existing formats so that no new loading branch is needed:

| Format | Loaded by | Used by |
|---|---|---|
| Merged weights, or a PEFT adapter (`adapter_config.json`) at the checkpoint root | The default branch: `load_model(load_from="local-checkpoint")`, which detects adapters | `pwd`, `circuitbreak`, `robpwd`, `distcot`, `distcot_lat`, `obfcot` |
| `config.json` with `system_prompt`, no weights | Base model plus the prompt | `sysprompt` |
| Weights plus `organism_config.json` with `system_prompt` | Checkpoint plus the prompt | `robsysprompt` |
| `config.json` with `adapter_subdirs` and `attachment_strategy` | `organisms/_adapter_merging.py:attach_adapter_to_model` on the base model | `robadapt` |

The orchestrator treats a checkpoint as finished when it contains `config.json`,
`organism_config.json`, `adapter_config.json` or `*.safetensors`; organisms with a recipe record
(`experiments/provenance.py:MARKER_FILES`) also need a valid record (`experiments/run.py:_ckpt_complete`).

### Step 2: register it

| File | What to add |
|---|---|
| `organisms/__init__.py` | Import the classes and list them in `__all__`. |
| `train.py` | The id in the `--model_org` choices in `parse_args`, any new flags there, and a branch in `_build_organism(args, multi)` that builds the single or `MultiDataset*` class (the `common_sft` and `common_lat` dicts hold the shared arguments). Checkpoint directories are named `{model_org}_{model}_{datasets}`, so prefer an id without `_`; `evaluate.py:_parse_model_path` also accepts two-token ids such as `distcot_lat`. |
| `HYPERPARAMETERS.toml` | Sections `[my_org]`, `[my_org."<model>"]` and `[my_org."<model>".<dataset>]`, merged most-specific-wins by `utils/hparams.py:resolve_hparams` (a multi-dataset key is the sorted ids joined by `_`). `train.py:_file_defaults` applies the result as argparse defaults, so a key that names a `train.py` flag becomes that flag's default (explicit CLI flags still win) and reaches the organism through `_build_organism`. Other keys are read only where code asks for them, as `robust_password_lock.py:_apply_validated_recipe_defaults` does for the robust organisms' recipe keys. |
| `evaluate.py` | The id in `_VALID_MODEL_ORGS`. A new checkpoint format needs a branch in both `_load_mo_model` and `_eval_multi`. Add the id to `_WHITEBOX_ORGANISMS` if whitebox techniques may run on it. Organisms without a trigger skip the with-trigger score in `_eval_task_mo` (as `robadapt` does). System-prompt organisms are matched by the literal `("sysprompt", "robsysprompt")` checks in `_run_elicitations_single` and `main`; every other organism gets `_PWD_CB_TECHNIQUES`, or `_COT_TECHNIQUES` when it is in `_WHITEBOX_ORGANISMS`. |
| `experiments/naming.py` | An `ORGANISMS` entry: `Organism(code, paper, role, profile, ...)` with `needs_training`, `depends_on` (a prerequisite organism, as CktBr depends on its PWD parent), `needs_cot_data`, `train_code` and `train_args` as needed. Role `"proposed"` or `"extra"` (exploratory) puts it in `PROPOSED_ORGANISMS` (generate-and-parse protocol); a prior baseline also belongs in `PRIOR_TABLE_ORGANISMS`. Add it to a list in `TABLE_ORGANISMS` to include it in a table (a new table also needs `TABLE_MODELS` and `TABLE_TASKS` entries), and to `AUTODAN_ONLY_REPORT_ORGANISMS` if its reported blackbox number is AutoDAN only. |
| `experiments/recipes.py` | Any `WCMO_*` settings the recipe needs, in `recipe_env` (compare the `_ROBCOT` block for `obfcot`). |

## 3. Adding an elicitation technique

### Step 1: the technique class

Subclass `elicitation/base.py:ElicitationTechnique` in `elicitation/blackbox/` (query access) or
`elicitation/whitebox/` (weights, gradients or activations). Set `SUPPORTED_DATASETS` and
`elicitation_type`, and implement `run(eval_data, train_data=None, **kwargs)`, returning a dict with
`"accuracy"` (a fraction) and optionally `"n_eval"` (the number of items scored).

Score with the base-class readouts so that elicitation accuracy is comparable to the
effectiveness evaluation: `chat_format_column` applies the chat template, `_forced_choice_accuracy`
scores fully formatted prompts (generate-and-parse by default, forced choice with
`WCMO_GENPARSE=0`, MATH always parsed), and `_genparse_accuracy_df` scores raw questions.

```python
# elicitation/blackbox/my_attack.py
import pandas as pd

from ..base import ElicitationTechnique, ElicitationType
from ...data.utils import set_seed
from ...utils.column_utils import BASE_QUESTION_COLUMN


class MyAttack(ElicitationTechnique):
    SUPPORTED_DATASETS = ("wmdp", "bio", "chem", "cyber",
                          "mmlu", "mmlu-prof", "mmlu-stem", "mmlu-hss")
    elicitation_type = ElicitationType.BLACKBOX

    def run(self, eval_data: pd.DataFrame, train_data: pd.DataFrame | None = None, **kwargs):
        set_seed(self.seed)
        df = eval_data.copy()
        df["attack_prompt"] = df[BASE_QUESTION_COLUMN].map(lambda q: q)  # build the attack here
        df, col = self.chat_format_column(df, "attack_prompt")
        n_correct, n = self._forced_choice_accuracy(df, col)
        return {"accuracy": n_correct / n, "n_eval": n}
```

How `evaluate.py:_run_elicitations_single` calls a technique:

* `run()` is called once on the validation set and once on the test split, each time with
  `train_data` set to `elicit_train`. Fit only on `train_data`; never select on the test split.
* A whitebox technique may modify the model in place. Its test pass runs on a freshly reloaded
  organism unless the name is in `_NO_RELOAD_TEST_TECHNIQUES` (inference-only techniques that
  restore the weights exactly).
* A technique that fits once and measures both splits can define
  `run_splits(eval_val, eval_test, train_data)` returning `(val_result, test_result)`, as
  `ProbeElicitation` does.
* Fine-tuning techniques that report a training curve can add `"elicited_at_step"` and
  `"final_accuracy"` to the result; list the name in `_STEP_TRACKED_TECHNIQUES` so they are printed.
  `ElicitationTechnique.assert_step0_lock_measured` checks that the step-0 measurement shows the lock.
* Whitebox fine-tuning techniques that need generic safety data can reuse
  `elicitation/whitebox/_hhh_data.py` (`load_hhh_sft`, `load_hhh_pairs`, `load_hhh_prompts`,
  `is_refusal`).

### Step 2: register it

| File | What to add |
|---|---|
| `elicitation/blackbox/__init__.py` or `elicitation/whitebox/__init__.py` | Import the class and list it in `__all__`. |
| `evaluate.py` | A branch in `_make_technique`, and the name in one technique set: `_PWD_CB_TECHNIQUES` (blackbox, trigger-based organisms), `_WHITEBOX_TECHNIQUES`, or `_SYSTEM_PROMPT_TECHNIQUES`. The sets feed `_COT_TECHNIQUES`, `_ALL_TECHNIQUES` (the `--elicitations` choices) and the per-organism compatibility checks. Both the branch and the set membership are required. Optional: `_VAL_SKIPPABLE_TECHNIQUES` or `_VAL_SKIPPABLE_SWEEP_TECHNIQUES` when the validation pass feeds no reported number, and `_NO_RELOAD_TEST_TECHNIQUES` as above. |
| `train.py` | The name in `_ALL_ELICITATIONS` (choices for inline `--evaluate`). |
| `HYPERPARAMETERS.toml` | For a blackbox attack, an `[attack.my_attack]` section (per-model overrides in `[attack.my_attack."<model>"]`) plus a `_ATTACK_HPARAM_ENV["my_attack"]` entry in `evaluate.py` mapping each key to the `WCMO_*` variable your constructor reads. `_make_technique` passes only `model`, `tokenizer`, `dataset`, `model_name` and `seed`, so other settings come from environment variables (precedence: environment, then the TOML file, then the code default). |
| `experiments/naming.py` | A whitebox name in `WHITEBOX_TECHNIQUES` (the orchestrator splits blackbox and whitebox phases on it). To include it in the whitebox tables, add it to `PAPER_WHITEBOX_TECHNIQUES` and `TECHNIQUE_LABELS`. Which techniques each table requests is decided by `elicitations_for`; jailbreak-style attacks are grouped in `JAILBREAK_TECHNIQUES`. |
| `experiments/recipes.py` | Sweep or protocol settings the technique needs (compare `_WHITEBOX`). |

Two routing details:

* `jailbreak` on the command line expands to `gcg autodan tap` in `evaluate.py:_expand_elicitations`.
* System-prompt organisms run attacks through `PromptInjection.run`, which handles only
  `prompt_injection`, `gcg`, `autodan` and `tap`, and `_run_elicitations_single` filters the
  requested names to that set. A new name in `_SYSTEM_PROMPT_TECHNIQUES` needs support in both
  places, or it passes validation without running.

## 4. Adding a base model

Add the short name to `utils/loading.py:MODEL_NAME_TO_HF_PATH` and mirror it in `train.py:_MODEL_CHOICES`.
The name must contain no `_` and must not end in a dataset id, or `evaluate.py:_parse_model_path`
mis-splits checkpoint directories. For the orchestrator, add it to `QWEN_MODELS` or `FAMILY_MODELS`
(with a `tier`) and to `TABLE_MODELS` in `experiments/naming.py`. Per-model rendering and memory
settings live in `utils/loading.py` (`REASONING_MODELS`, `ANSWER_PREFILL_MODELS`,
`MAX_EVAL_BATCH_SIZE`), and per-model hyperparameters go in quoted `HYPERPARAMETERS.toml` sections.
