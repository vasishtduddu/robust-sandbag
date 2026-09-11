"""Build datasets/mcqa/commonsense_anchor_train.csv from PIQA + Winogrande.

This file is the LAT KL-to-base commonsense retain (data/utility_mix.load_commonsense_anchor).
It had NO builder in the repo: it existed only as a artifact under datasets/, so when the
2026-09-09 migration dropped datasets/ nothing regenerated it and load_commonsense_anchor silently
warned and returned None. The eight banked MCQ cells trained WITH this channel (1170 rows on
qwen3.8/wmdp); the first MATH cell trained without it, which is an inconsistent recipe across
cells, so it has to be reproducible rather than hand-made.

PIQA and Winogrande are deliberately NOT HellaSwag or CommonsenseQA: utility is REPORTED on those
two, and training on them would be teach-to-test. Both sources here are two-choice, so rows render
A/B in the same shape hellaswag_train.csv uses (stem, lettered options, trailing "Answer:").

    python scripts/build_commonsense_anchor.py
"""

import csv
import pathlib

import pandas as pd
from datasets import load_dataset

OUT = pathlib.Path(__file__).resolve().parent.parent / "datasets" / "mcqa" / "commonsense_anchor_train.csv"
N_PER_SOURCE = 2000


def render(stem: str, options: list[str]) -> str:
    body = "\n".join(f"{chr(65 + i)}. {o}" for i, o in enumerate(options))
    return f"{stem.strip()}\n\n{body}\n\nAnswer:"


def main() -> None:
    rows = []

    # ybisk/piqa still ships a loading script, which datasets 4.x refuses to run;
    # baber/piqa is the same data as parquet.
    piqa = load_dataset("baber/piqa", split="train")
    for r in piqa.select(range(min(N_PER_SOURCE, len(piqa)))):
        rows.append({"question_prompt": render(r["goal"], [r["sol1"], r["sol2"]]),
                     "answer": "AB"[int(r["label"])], "source": "piqa"})

    wino = load_dataset("allenai/winogrande", "winogrande_xl", split="train")
    for r in wino.select(range(min(N_PER_SOURCE, len(wino)))):
        rows.append({"question_prompt": render(r["sentence"], [r["option1"], r["option2"]]),
                     "answer": "AB"[int(r["answer"]) - 1], "source": "winogrande"})

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, quoting=csv.QUOTE_ALL)
    print(f"wrote {OUT} ({len(df)} rows: {df['source'].value_counts().to_dict()})")


if __name__ == "__main__":
    main()
