"""Code generation dataset for sandbagging evaluation.

Uses the APPS (Automated Programming Progress Standard) dataset from
codeparrot/apps on HuggingFace. Competition-level problems and rows without
test cases are excluded.

Splits:
  finetune_base  — 3000 rows for fine-tuning the base model
  mos_train      — 3000 rows for model organism training
  elicit_train   — 1000 rows for elicitation method training
  eval           — 500 rows for evaluation during training
  test           — 944 rows (399 with solutions + 545 without) for final evaluation

All splits from df_with_solutions are non-overlapping by URL (asserted at split time).
"""

from pathlib import Path

import pandas as pd
from datasets import load_dataset

from .base import SandbagDataset
from .utils import set_seed

_DATA_ROOT = Path(__file__).parent.parent / "datasets"


# Fixed split sizes (preserve exactly to match original paper)
N_BASE_SFT = 3000
N_MOS_TRAIN = 3000
N_ELICIT_TRAIN = 1000
N_EVAL = 500
N_TEST_WITH_SOLUTIONS = 399
N_TEST_NO_SOLUTIONS = 545


class CodeGenDataset(SandbagDataset):
    """APPS code generation dataset for sandbagging experiments.

    Models are trained to solve programming problems. Sandbagging manifests as
    deliberately producing incorrect or incomplete code solutions.

    Usage:
        ds = CodeGenDataset()
        ds.load(seed=42)
        test_df = ds.get_split("test")
        train_df = ds.get_split("mos_train")

    Elicitation compatibility:
        Only 'prefilling' and 'sft' elicitation methods work with this dataset.
        MCQA-specific techniques (many_shot, gcg, steering, melbo, anti_refusal)
        are not applicable.

    Adding new code generation datasets:
        Subclass SandbagDataset. Ensure test rows are URL-disjoint from train rows
        to prevent data leakage.
    """

    SPLIT_NAMES = ("finetune_base", "mos_train", "elicit_train", "eval", "test")

    def __init__(self) -> None:
        super().__init__()

    def load(self, seed: int = 42, filter_long_questions: bool = False) -> None:
        """Download APPS from HuggingFace and create the standard splits.

        Filters out competition-level problems and rows with empty input_output.
        Splits are fixed-size (not fractional) to match the original paper.
        """
        set_seed(seed)

        # The codeparrot/apps dataset script on HuggingFace Hub has non-UTF-8
        # content which causes datasets>=2.x to fail during module inspection.
        # Patch HfFileSystem.open to use errors='replace' for that one check.
        try:
            from huggingface_hub import HfFileSystem as _HfFS
            _orig_hffs_open = _HfFS.open
            def _patched_hffs_open(self, path, mode="rb", *args, **kwargs):
                if mode == "r" and kwargs.get("encoding") == "utf-8":
                    kwargs["errors"] = "replace"
                return _orig_hffs_open(self, path, mode, *args, **kwargs)
            _HfFS.open = _patched_hffs_open
            raw = load_dataset("codeparrot/apps")
        finally:
            # Restore original method regardless of success or failure
            try:
                _HfFS.open = _orig_hffs_open
            except NameError:
                pass
        train_df = pd.DataFrame(raw["train"])
        test_df = pd.DataFrame(raw["test"])
        combined_df = pd.concat([train_df, test_df], ignore_index=True)

        # Exclude competition-level problems and rows without test cases (single pass)
        mask = (combined_df["difficulty"] != "competition") & (
            combined_df["input_output"].fillna("").apply(len) > 0
        )
        filtered_df = combined_df[mask]

        # Separate rows with and without solutions
        has_solution = filtered_df["solutions"].fillna("") != ""
        df_with_solutions = filtered_df[has_solution].reset_index(drop=True)
        df_no_solutions = filtered_df[~has_solution].reset_index(drop=True)

        # Shuffle with fixed seed for reproducibility
        df_with_solutions = df_with_solutions.sample(frac=1.0, random_state=seed)
        df_no_solutions = df_no_solutions.sample(frac=1.0, random_state=seed)

        # Create fixed-size splits from df_with_solutions
        finetune_base_df = df_with_solutions.iloc[:N_BASE_SFT]
        mos_train_df = df_with_solutions.iloc[N_BASE_SFT:N_BASE_SFT + N_MOS_TRAIN]
        elicit_train_df = df_with_solutions.iloc[
            N_BASE_SFT + N_MOS_TRAIN : N_BASE_SFT + N_MOS_TRAIN + N_ELICIT_TRAIN
        ]
        eval_split_df = df_with_solutions.iloc[
            N_BASE_SFT + N_MOS_TRAIN + N_ELICIT_TRAIN : N_BASE_SFT + N_MOS_TRAIN + N_ELICIT_TRAIN + N_EVAL
        ]

        # Verify sizes
        assert len(finetune_base_df) == N_BASE_SFT
        assert len(mos_train_df) == N_MOS_TRAIN
        assert len(elicit_train_df) == N_ELICIT_TRAIN
        assert len(eval_split_df) == N_EVAL

        # Test set: last N_TEST_WITH_SOLUTIONS rows + N_TEST_NO_SOLUTIONS from no-solutions pool
        available = len(df_with_solutions) - N_BASE_SFT - N_MOS_TRAIN - N_ELICIT_TRAIN - N_EVAL
        if available < N_TEST_WITH_SOLUTIONS:
            raise RuntimeError(
                f"Not enough rows with solutions for test split: need {N_TEST_WITH_SOLUTIONS}, "
                f"got {available}. The APPS dataset may have changed on HuggingFace."
            )
        test_from_solutions = df_with_solutions.iloc[-N_TEST_WITH_SOLUTIONS:]
        test_from_no_solutions = df_no_solutions.iloc[:N_TEST_NO_SOLUTIONS]
        final_test_df = pd.concat(
            [test_from_solutions, test_from_no_solutions], ignore_index=True
        ).reset_index(drop=True)

        # Assert no URL overlap between test and training splits
        test_urls = set(final_test_df["url"])
        assert len(test_urls.intersection(set(finetune_base_df["url"]))) == 0
        assert len(test_urls.intersection(set(mos_train_df["url"]))) == 0
        assert len(test_urls.intersection(set(elicit_train_df["url"]))) == 0
        assert len(test_urls.intersection(set(eval_split_df["url"]))) == 0

        self.splits["finetune_base"] = finetune_base_df.reset_index(drop=True)
        self.splits["mos_train"] = mos_train_df.reset_index(drop=True)
        self.splits["elicit_train"] = elicit_train_df.reset_index(drop=True)
        self.splits["eval"] = eval_split_df.reset_index(drop=True)
        self.splits["test"] = final_test_df

        self._loaded = True

    def get_split(self, split: str) -> pd.DataFrame:
        """Return the DataFrame for the given split.

        Args:
            split: One of "finetune_base", "mos_train", "elicit_train", "eval", "test".
        """
        self._require_loaded()
        if split not in self.splits:
            raise ValueError(
                f"Unknown split '{split}'. Choose from {self.SPLIT_NAMES}"
            )
        return self.splits[split]

    def save(self, directory: str = None) -> None:
        """Save all splits as CSV files under the given directory."""
        self._require_loaded()
        if directory is None:
            directory = str(_DATA_ROOT / "code")
        out_dir = self._ensure_dir(directory)

        filenames = {
            "finetune_base": "apps_finetune_base_models.csv",
            "mos_train": "apps_mos_train.csv",
            "elicit_train": "apps_elicit_train.csv",
            "eval": "apps_eval.csv",
            "test": "apps_test.csv",
        }
        for split_name, filename in filenames.items():
            self.splits[split_name].to_csv(out_dir / filename, index=False)
        print(f"Saved CodeGen splits to {out_dir}/")

    @classmethod
    def from_directory(cls, directory: str = None) -> "CodeGenDataset":
        """Load pre-processed CodeGen splits from CSV files (skips HF download)."""
        if directory is None:
            directory = str(_DATA_ROOT / "code")
        ds = cls.__new__(cls)
        super(CodeGenDataset, ds).__init__()
        data_dir = Path(directory)

        filenames = {
            "finetune_base": "apps_finetune_base_models.csv",
            "mos_train": "apps_mos_train.csv",
            "elicit_train": "apps_elicit_train.csv",
            "eval": "apps_eval.csv",
            "test": "apps_test.csv",
        }
        for split_name, filename in filenames.items():
            path = data_dir / filename
            if not path.exists():
                raise FileNotFoundError(
                    f"Expected {path}. Run CodeGenDataset().load() and .save() first."
                )
            ds.splits[split_name] = pd.read_csv(path)

        ds._loaded = True
        return ds
