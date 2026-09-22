"""Render per-seed AutoDAN robustness boxplots for paper Tables 1 and 2.

The main paper figure combines both tables. It uses datasets as six small
multiples (WMDP across the top, MMLU across the bottom), with the three prior
organisms on the left and the three proposed organisms on the right. Model size
is a color-coded/dodged series, and individual seed points are drawn on top of
every box so three-seed variation remains explicit.

The generator applies the table loader's scoring-protocol, seed-roster,
checkpoint-receipt, count, fingerprint, and attack-overlay checks. It only
reads experiment artifacts and writes a figure plus its audit CSV; it never
enters the normal table-compilation pipeline.

Examples::

    venv/bin/python -m experiments.jailbreak_boxplots --table prior \
      --artifact-root artifacts/compose-prior

    venv/bin/python -m experiments.jailbreak_boxplots --table ours \
      --artifact-root /scratch/markwang/possum-table3-current \
      --artifact-root /scratch/markwang/possum-mmlu-merge \
      --allow-incomplete

    venv/bin/python -m experiments.jailbreak_boxplots --table combined

RobPWD and RobAdapt remain visible template slots in the proposed-organism
figure.  Once their validated artifacts are available, pass
``--include-ours robpwd robadapt obfcot`` to populate them without changing the
layout or plotting code.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from . import naming, plot_style, provenance, tablefill
except ImportError:
    import naming, plot_style, provenance, tablefill


ROOT = Path(__file__).resolve().parent.parent
TASKS = tuple(naming.TASKS)
MODELS = tuple(naming.TABLE_MODELS["ours"])
SEEDS = tuple(naming.TABLE3_TRAINED_SEEDS)

# This published three-seed cell was explicitly confirmed as 0.0 (0.0), while
# its locally visible AutoDAN fields were later withdrawn. Attack accuracy is
# nonnegative, so a three-seed mean and standard error of exactly zero uniquely
# imply that every seed is zero. Keep the reconstruction explicit in the CSV.
PUBLISHED_ZERO_SEED_CELLS = {
    ("obfcot", "qwen2.5-7b-it", "mmlu-hss"): SEEDS,
}

# Colorblind-safe, print-friendly extension of the house palette.  Green is the
# existing robust-method green; brown is the existing baseline brown; blue is a
# restrained third hue needed to distinguish all three model sizes.
MODEL_STYLE = {
    "qwen2.5-7b-it": ("7B", "#3B6FB6"),
    "qwen2.5-14b-it": ("14B", plot_style.BROWN),
    "qwen2.5-32b-it": ("32B", plot_style.GREEN),
}

PRIOR_ORGANISMS = tuple(naming.TABLE_ORGANISMS["prior"])
OURS_ORGANISMS = tuple(naming.TABLE_ORGANISMS["ours"])
COMBINED_ORGANISMS = PRIOR_ORGANISMS + OURS_ORGANISMS
# A deliberate gutter makes the prior/ours split readable without adding a
# second visual encoding to the already color-coded model-size boxes.
COMBINED_X = dict(zip(COMBINED_ORGANISMS, (0.0, 1.0, 2.0, 3.8, 4.8, 5.8)))


@dataclass(frozen=True)
class Point:
    table: str
    organism: str
    model: str
    dataset: str
    seed: int
    accuracy: float
    source: str = "artifact"


def _configure_loader(artifact_root: Path, attack_roots: Iterable[Path]) -> None:
    artifact_root = artifact_root.expanduser().resolve()
    tablefill.ARTIFACT_ROOT = artifact_root
    tablefill.RESULTS_JSON_ROOT = artifact_root / "results" / "json"
    tablefill.ATTACK_RESULTS_JSON_ROOTS = tuple(
        root.expanduser().resolve() / "results" / "json" for root in attack_roots
    )


def _result_matches_checkpoint_receipt(
    blob: dict,
    checkpoint: Path,
    organism: str,
    model: str,
    datasets: tuple[str, ...],
    seed: int,
) -> bool:
    """Validate the exact checkpoint stamp without repeatedly hashing weights.

    The marker checkpoint_files receipt was SHA-256'd when the checkpoint was
    published. We bind the result to that receipt exactly. Curated prior-work
    compositions may retain a result receipt after retiring the large weights.
    """
    if not provenance.result_identity_matches(
        blob,
        result_org=organism,
        model=model,
        datasets=datasets,
        seed=seed,
    ):
        return False
    recorded = blob.get("checkpoint_provenance")
    expected_identity = {
        "schema_version": provenance.SCHEMA_VERSION,
        "checkpoint_organism": organism,
        "model": model,
        "datasets": list(datasets),
        "seed": seed,
        "checkpoint_relpath": provenance._relative_checkpoint_name(checkpoint),
    }
    if not isinstance(recorded, dict) or any(
        recorded.get(key) != value for key, value in expected_identity.items()
    ):
        return False
    marker = provenance.marker_path(checkpoint, organism)
    if marker is None or not marker.is_file():
        # Some curated prior-work composition links retain accepted result
        # receipts after the large source checkpoint has been retired. The
        # receipt is still sufficient for plotting the already-published raw
        # result; RoBCoTDstl always requires its live marker below.
        return organism in naming.PRIOR_TABLE_ORGANISMS
    try:
        config = json.loads(marker.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not provenance.validate_checkpoint_config(
        organism,
        config,
        model=model,
        datasets=datasets,
        seed=seed,
    ):
        return False

    payload_receipt = config.get("checkpoint_files")
    if organism == "obfcot" and payload_receipt is None:
        return False

    stamp = {
        "schema_version": provenance.SCHEMA_VERSION,
        "checkpoint_organism": organism,
        "model": model,
        "datasets": list(datasets),
        "seed": seed,
        "checkpoint_relpath": provenance._relative_checkpoint_name(checkpoint),
        "marker_file": marker.name,
        "marker_sha256": provenance._canonical_sha256(config),
        "recipe_id": config.get("recipe", config.get("prompt_variant")),
    }
    if payload_receipt is not None:
        stamp["checkpoint_payload_sha256"] = provenance._canonical_sha256(
            {"checkpoint_files": payload_receipt}
        )
    return recorded == stamp


def _load_results_for_figure(
    organism: str, model: str, datasets: tuple[str, ...]
) -> list[dict]:
    """Strict three-seed loader specialized to the two robustness figures."""
    expected_seeds = (
        naming.PRIOR_SYSPROMPT_INFERENCE_SEEDS
        if organism == "sysprompt"
        else (
            naming.PRIOR_TRAINED_SEEDS
            if organism in naming.PRIOR_TABLE_ORGANISMS
            else naming.TABLE3_TRAINED_SEEDS
        )
    )
    base = naming.ckpt_dirname(organism, model, datasets)
    expected_paths = {
        tablefill.RESULTS_JSON_ROOT
        / naming.result_json_name(organism, model, datasets, seed)
        for seed in expected_seeds
    }
    if organism in naming.TABLE3_ORGANISMS:
        discovered = set(tablefill.RESULTS_JSON_ROOT.glob(base + "__seed*.json"))
        legacy = tablefill.RESULTS_JSON_ROOT / (base + ".json")
        if legacy.exists():
            discovered.add(legacy)
        if discovered != expected_paths:
            return []

    blobs: list[dict] = []
    for seed in expected_seeds:
        path = (
            tablefill.RESULTS_JSON_ROOT
            / naming.result_json_name(organism, model, datasets, seed)
        )
        try:
            blob = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        checkpoint = provenance.canonical_checkpoint_dir(
            tablefill.ARTIFACT_ROOT / "checkpoints",
            organism,
            model,
            datasets,
            seed,
        )
        if (
            not tablefill._current_protocol_identity(
                blob, organism, model, datasets, seed
            )
            or blob.get("elicit_rng_seed") is not None
            or not _result_matches_checkpoint_receipt(
                blob, checkpoint, organism, model, datasets, seed
            )
            or naming.metric_util_csqa(blob, None) is None
            or naming.metric_util_hs(blob, None) is None
        ):
            return []
        nopw = naming._read_metric(blob, "task_nopw_acc", prefix="val_")
        pw = naming._read_metric(blob, "task_pw_acc", prefix="val_")
        if nopw is not None and nopw < 0.01 and pw is not None and pw < 0.01:
            return []
        blobs.append(
            tablefill._overlay_autodan(
                blob,
                organism,
                model,
                datasets,
                seed,
                path.name,
            )
        )
    return blobs


def collect_points(
    table: str,
    artifact_roots: Path | Iterable[Path],
    attack_roots: Iterable[Path],
    include_ours: tuple[str, ...] = ("obfcot",),
    *,
    allow_incomplete: bool = False,
) -> list[Point]:
    """Load validated per-seed values, with later reporting roots taking precedence."""
    if table not in {"prior", "ours"}:
        raise ValueError(f"unsupported table {table!r}")
    if isinstance(artifact_roots, Path):
        reporting_roots = (artifact_roots,)
    else:
        reporting_roots = tuple(artifact_roots)
    if not reporting_roots:
        raise ValueError("at least one artifact root is required")
    attack_roots = tuple(attack_roots)
    overlay_roots = tuple(dict.fromkeys((*reporting_roots, *attack_roots)))

    organisms = tuple(naming.TABLE_ORGANISMS[table])
    included = set(organisms if table == "prior" else include_ours)
    unknown = included.difference(organisms)
    if unknown:
        raise ValueError(f"organisms are not in the {table} table: {sorted(unknown)}")

    points: list[Point] = []
    missing: list[str] = []
    for organism in organisms:
        if organism not in included:
            continue
        for model in MODELS:
            for dataset in TASKS:
                blobs: list[dict] = []
                for root in reversed(reporting_roots):
                    _configure_loader(root, overlay_roots)
                    candidate = _load_results_for_figure(
                        organism, model, (dataset,)
                    )
                    if candidate:
                        blobs = candidate
                        break

                blob_seeds = tuple(sorted(blob.get("seed") for blob in blobs))
                if len(blobs) != len(SEEDS) or blob_seeds != SEEDS:
                    missing.append(
                        f"{organism}/{model}/{dataset}: "
                        f"{len(blobs)} validated base blobs, seeds={blob_seeds}"
                    )
                    continue

                values_by_seed = {
                    blob["seed"]: naming.metric_rob_jlbrk(blob, dataset)
                    for blob in blobs
                }
                available = {
                    seed: value
                    for seed, value in values_by_seed.items()
                    if value is not None
                }
                published_zero_seeds = PUBLISHED_ZERO_SEED_CELLS.get(
                    (organism, model, dataset), ()
                )
                if not available and published_zero_seeds:
                    available = {seed: 0.0 for seed in published_zero_seeds}
                if tuple(sorted(available)) != SEEDS:
                    missing_seeds = sorted(set(SEEDS).difference(available))
                    missing.append(
                        f"{organism}/{model}/{dataset}: "
                        f"missing AutoDAN seeds={missing_seeds}"
                    )
                for seed, value in sorted(available.items()):
                    source = (
                        "published_zero_cell"
                        if seed in published_zero_seeds
                        else "artifact"
                    )
                    points.append(
                        Point(
                            table,
                            organism,
                            model,
                            dataset,
                            seed,
                            float(value),
                            source,
                        )
                    )

    if missing and not allow_incomplete:
        detail = "\n  ".join(missing)
        raise RuntimeError(
            "boxplots require a complete, validated three-seed cohort for every "
            f"requested cell:\n  {detail}"
        )
    if missing:
        print("[jailbreak-boxplots] incomplete cells (shown with n/3 labels):")
        for item in missing:
            print(f"  {item}")
    return points


def write_csv(points: list[Point], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "table",
                "organism",
                "model",
                "dataset",
                "seed",
                "attack_accuracy",
                "source",
            ]
        )
        for point in points:
            writer.writerow(
                [
                    point.table,
                    point.organism,
                    point.model,
                    point.dataset,
                    point.seed,
                    f"{100.0 * point.accuracy:.6f}",
                    point.source,
                ]
            )


def read_csv(path: Path) -> list[Point]:
    """Read an audited seed-level CSV written by :func:`write_csv`.

    CSV attack accuracies are percentages for direct human inspection; Points
    store fractions, matching the experiment JSON representation.
    """
    points: list[Point] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            points.append(
                Point(
                    table=row["table"],
                    organism=row["organism"],
                    model=row["model"],
                    dataset=row["dataset"],
                    seed=int(row["seed"]),
                    accuracy=float(row["attack_accuracy"]) / 100.0,
                    source=row.get("source") or "artifact",
                )
            )
    return points


def organisms_for_plot(table: str) -> tuple[str, ...]:
    if table == "combined":
        return COMBINED_ORGANISMS
    return tuple(naming.TABLE_ORGANISMS[table])


def render_boxplot(
    table: str,
    points: list[Point],
    out_dir: Path,
    include_ours: tuple[str, ...] = ("obfcot",),
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    plot_style.apply()
    organisms = organisms_for_plot(table)
    included = set(PRIOR_ORGANISMS)
    if table == "ours":
        included = set(include_ours)
    elif table == "combined":
        included.update(
            point.organism for point in points if point.organism in OURS_ORGANISMS
        )
    by_cell: dict[tuple[str, str, str], list[Point]] = {}
    for point in points:
        by_cell.setdefault((point.organism, point.model, point.dataset), []).append(point)

    is_combined = table == "combined"
    fig_width = 18.8 if is_combined else 15.6
    fig, axes = plt.subplots(2, 3, figsize=(fig_width, 9.2), sharex=True, sharey=True)
    x_positions = (
        COMBINED_X if is_combined else dict(zip(organisms, range(len(organisms))))
    )
    x_centres = [x_positions[organism] for organism in organisms]
    offsets = dict(zip(MODELS, (-0.23, 0.0, 0.23)))
    seed_jitter = dict(zip(SEEDS, (-0.035, 0.0, 0.035)))

    for ax, dataset in zip(axes.flat, TASKS):
        if is_combined:
            ax.axvspan(-0.55, 2.55, color=plot_style.BROWN, alpha=0.035, zorder=0)
            ax.axvspan(3.25, 6.35, color=plot_style.GREEN, alpha=0.035, zorder=0)
            ax.axvline(2.9, color="#b5b5b5", linewidth=1.0, linestyle="--", zorder=1)
            ax.text(
                1.0,
                1.015,
                "Prior work",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="bottom",
                color=plot_style.BROWN,
                fontsize=13,
                fontweight="semibold",
            )
            ax.text(
                4.8,
                1.015,
                "Ours",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="bottom",
                color=plot_style.GREEN,
                fontsize=13,
                fontweight="semibold",
            )

        for organism in organisms:
            organism_position = x_positions[organism]
            if organism not in included:
                ax.text(
                    organism_position,
                    3.0,
                    "pending",
                    ha="center",
                    va="bottom",
                    color="#888888",
                    fontsize=10.5 if is_combined else plot_style.ANNOT_SIZE - 2,
                )
                continue
            for model in MODELS:
                cell = sorted(
                    by_cell.get((organism, model, dataset), []),
                    key=lambda point: point.seed,
                )
                label, color = MODEL_STYLE[model]
                position = organism_position + offsets[model]
                if organism in OURS_ORGANISMS and len(cell) != len(SEEDS):
                    ax.text(
                        position,
                        101.0 - 4.0 * MODELS.index(model),
                        f"n={len(cell)}/3",
                        ha="center",
                        va="top",
                        color=color,
                        fontsize=9,
                        bbox={
                            "facecolor": "white",
                            "edgecolor": "none",
                            "alpha": 0.82,
                            "pad": 0.5,
                        },
                        zorder=6,
                    )
                if not cell:
                    continue
                values = [100.0 * point.accuracy for point in cell]
                ax.boxplot(
                    [values],
                    positions=[position],
                    widths=0.18,
                    patch_artist=True,
                    manage_ticks=False,
                    showfliers=True,
                    boxprops={
                        "facecolor": color,
                        "edgecolor": color,
                        "alpha": 0.28,
                        "linewidth": 1.7,
                    },
                    medianprops={"color": color, "linewidth": 2.3},
                    whiskerprops={"color": color, "linewidth": 1.4},
                    capprops={"color": color, "linewidth": 1.4},
                    flierprops={
                        "marker": "o",
                        "markerfacecolor": color,
                        "markeredgecolor": "white",
                        "markersize": 5,
                        "alpha": 0.9,
                    },
                )
                for point in cell:
                    ax.scatter(
                        position + seed_jitter[point.seed],
                        100.0 * point.accuracy,
                        s=31,
                        color=color,
                        edgecolor="white",
                        linewidth=0.65,
                        zorder=4,
                    )

        ax.set_title(naming.TASKS[dataset], pad=28 if is_combined else 8)
        ax.set_xlim(-0.55, 6.35 if is_combined else len(organisms) - 0.45)
        ax.set_ylim(-1.5, 103.0)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_xticks(x_centres)
        ax.set_xticklabels(
            [naming.ORGANISMS[org].paper for org in organisms],
            fontsize=(
                12.5
                if is_combined
                else (15 if table == "ours" else plot_style.TICK_SIZE)
            ),
            rotation=24 if is_combined else 0,
            ha="right" if is_combined else "center",
            rotation_mode="anchor" if is_combined else None,
        )
        ax.grid(axis="y", alpha=0.25, linewidth=0.8)
        ax.set_axisbelow(True)
        plot_style.despine(ax)

    fig.supylabel("AutoDAN attack accuracy (%)", x=0.012)
    fig.supxlabel("Model organism", y=0.012)
    handles = [
        Patch(facecolor=color, edgecolor=color, alpha=0.35, label=label)
        for label, color in MODEL_STYLE.values()
    ]
    fig.legend(
        handles=handles,
        title="Qwen2.5 model size",
        loc="upper center",
        bbox_to_anchor=(0.5, 1.015),
        ncol=3,
        frameon=False,
    )
    fig.subplots_adjust(
        left=0.065,
        right=0.995,
        bottom=0.17 if is_combined else 0.12,
        top=0.82 if is_combined else 0.87,
        wspace=0.14,
        hspace=0.42 if is_combined else 0.30,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"jailbreak_boxplot_{table}"
    paths: list[Path] = []
    for extension in ("pdf", "png"):
        path = stem.with_suffix(f".{extension}")
        fig.savefig(path, bbox_inches="tight", dpi=250)
        paths.append(path)
    plt.close(fig)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--table", choices=("prior", "ours", "combined"), required=True
    )
    parser.add_argument(
        "--artifact-root",
        action="append",
        type=Path,
        default=[],
        help=(
            "Reporting root containing results/json and checkpoints; repeat in "
            "canonical precedence order (later roots win)."
        ),
    )
    parser.add_argument(
        "--attack-artifact-root",
        action="append",
        default=[],
        type=Path,
        help="Execution root carrying matching AutoDAN fields; repeatable.",
    )
    parser.add_argument(
        "--include-ours",
        nargs="+",
        choices=tuple(naming.TABLE_ORGANISMS["ours"]),
        default=("obfcot",),
        help="Proposed rows to load. The x-axis always reserves all three slots.",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Render available seeds and label incomplete cells as n/3.",
    )
    parser.add_argument(
        "--prior-csv",
        type=Path,
        default=ROOT / "tables" / "jailbreak_boxplot_prior.csv",
        help="Audited prior-work seed CSV used by --table combined.",
    )
    parser.add_argument(
        "--ours-csv",
        type=Path,
        default=ROOT / "tables" / "jailbreak_boxplot_ours.csv",
        help="Audited proposed-organism seed CSV used by --table combined.",
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tables")
    args = parser.parse_args()

    if args.table == "combined":
        points = read_csv(args.prior_csv) + read_csv(args.ours_csv)
    else:
        if not args.artifact_root:
            parser.error("--artifact-root is required for --table prior/ours")
        points = collect_points(
            args.table,
            args.artifact_root,
            args.attack_artifact_root,
            tuple(args.include_ours),
            allow_incomplete=args.allow_incomplete,
        )
    csv_path = args.out_dir / f"jailbreak_boxplot_{args.table}.csv"
    write_csv(points, csv_path)
    paths = render_boxplot(args.table, points, args.out_dir, tuple(args.include_ours))
    print(f"[jailbreak-boxplots] wrote {csv_path} ({len(points)} seed values)")
    for path in paths:
        print(f"[jailbreak-boxplots] wrote {path}")


if __name__ == "__main__":
    main()
