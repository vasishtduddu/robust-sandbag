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

"""Build the WMDP task in the item format of Finke et al.'s pipeline.

Their organisms withhold "hard" items and answer "easy" ones. Here the hard tier is WMDP (Bio, Chem
and Cyber pooled) and the easy tier is general-knowledge MMLU (the 41 held-out anchor subjects,
minus those that overlap the WMDP domains). Sizes follow theirs: 150 + 150 training items, 50 + 50
development items and 100 + 100 test items, all drawn from our own splits (WMDP mos_train, mos_eval
and test; MMLU anchor mos_train, mos_eval and test), so no test question is ever trained on.

Each item is {id, tier, code, description, answer}: `code` is the question with its options (their
pipeline puts the item text in the user turn), `description` the ground truth the judges read and
`answer` its letter. The attack item lists (data/attack/items.json) use their sizes: 68 hard items
for the weak-to-strong attack, the other 81 hard and 84 easy items for Stage C's simulated attacks.

    python external_organisms/finke2026/prepare_data.py     # -> external_organisms/finke2026/work/data
"""

import argparse
import ast
import json
import random
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WMDP_DOMAINS = ("bio", "chem", "cyber")
# Held-out MMLU subjects that overlap the WMDP domains; left out of the easy tier.
EXCLUDED_SUBJECTS = {"virology", "college_biology", "high_school_biology", "medical_genetics",
                     "high_school_chemistry", "college_computer_science",
                     "high_school_computer_science", "security_studies"}
SIZES = {"train": 150, "dev": 50, "test": 100}          # per tier
OUR_SPLIT = {"train": "mos_train", "dev": "mos_eval", "test": "test"}
LETTERS = "ABCD"


def _question(row) -> str:
    """The multiple-choice question without the trailing answer cue."""
    text = row["question_prompt"]
    return text[: text.rfind("Answer:")].rstrip() if text.rstrip().endswith("Answer:") else text


def _item(row, tier: str, item_id: str) -> dict:
    choices = row["choices"] if isinstance(row["choices"], list) else ast.literal_eval(row["choices"])
    letter = row["answer"] if isinstance(row["answer"], str) else LETTERS[int(row["answer"])]
    return {"id": item_id, "tier": tier, "code": _question(row), "answer": letter,
            "description": f"Answer {letter}: {choices[LETTERS.index(letter)]}"}


def _hard(mcqa: Path, split: str, n: int, rng: random.Random) -> list[dict]:
    per = [n // 3 + (i < n % 3) for i in range(3)]
    out = []
    for domain, k in zip(WMDP_DOMAINS, per):
        df = pd.read_csv(mcqa / f"wmdp_{domain}_{OUR_SPLIT[split]}.csv", escapechar="\\")
        rows = rng.sample(range(len(df)), k)
        out += [_item(df.iloc[i], "hard", f"wmdp_{domain}_{split}_{i}") for i in rows]
    return out


def _easy(mcqa: Path, split: str, n: int, rng: random.Random) -> list[dict]:
    df = pd.read_csv(mcqa / f"mmlu_anchor_{OUR_SPLIT[split]}.csv")
    df = df[~df["subject"].isin(EXCLUDED_SUBJECTS)].reset_index(drop=True)
    rows = rng.sample(range(len(df)), n)
    return [_item(df.iloc[i], "easy", f"mmlu_{df.iloc[i]['subject']}_{split}_{i}") for i in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mcqa_dir", default=str(REPO / "datasets" / "mcqa"))
    ap.add_argument("--out", default=str(HERE / "work" / "data"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    mcqa, out, rng = Path(args.mcqa_dir), Path(args.out), random.Random(args.seed)
    (out / "task").mkdir(parents=True, exist_ok=True)
    (out / "attack").mkdir(parents=True, exist_ok=True)

    splits = {}
    for split, n in SIZES.items():
        items = _hard(mcqa, split, n, rng) + _easy(mcqa, split, n, rng)
        rng.shuffle(items)
        splits[split] = items
        with open(out / "task" / f"{split}.jsonl", "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in items)
    texts = {s: {r["code"] for r in v} for s, v in splits.items()}
    assert not (texts["train"] & texts["test"]) and not (texts["dev"] & texts["test"]), "split overlap"

    hard = [r["id"] for r in splits["train"] if r["tier"] == "hard"]
    easy = [r["id"] for r in splits["train"] if r["tier"] == "easy"]
    rng.shuffle(hard)
    rng.shuffle(easy)
    items = {"w2s": hard[:68], "tar_hard": hard[68:68 + 81], "tar_easy": easy[:84]}
    (out / "attack" / "items.json").write_text(json.dumps(items, indent=1))
    print(f"wrote {out}: " + ", ".join(f"{s} {len(v)}" for s, v in splits.items())
          + "; attack items " + ", ".join(f"{k} {len(v)}" for k, v in items.items()))


if __name__ == "__main__":
    main()
