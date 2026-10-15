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
    venv/bin/python -m experiments.jailbreak_boxplots --table combined --expanded  # one-column

RobPWD and RobAdapt are the teammate's rows; their per-seed AutoDAN values come
from ``tables/attack_per_seed.json`` via ``--table ours --teammate-json``.
RoBCoTDstl seeds carry the Mark-approved Jlbrk-only swaps
(``tables/approved_variants.json``), exactly as Table 2 does.
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
TASKS = tuple(naming.SANDBAG_TASKS)
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
# Uniform spacing (Vasisht 2026-10-07): the prior/ours split is carried by the dashed
# divider alone, so no gutter, no background tint and no section headings. Every organism
# then sits on the same pitch, with the air that used to be one wide gutter redistributed
# as a visible gap between all six groups (Vasisht 2026-10-07, second pass: the groups read
# as one continuous band of boxes at pitch 1.0).
COMBINED_PITCH = 1.90
#: Half-width of one organism's group of three boxes, and of the panel margin beside it.
COMBINED_GROUP_HALF = 0.77
COMBINED_BAND_HALF = 0.88


@dataclass(frozen=True)
class CombinedLayout:
    """Geometry of the combined figure; styling and data are shared by every layout."""

    suffix: str
    fig_width: float
    pitch: float
    band_half: float
    model_offset: float
    left: float

    def x_positions(self) -> dict[str, float]:
        return {organism: index * self.pitch for index, organism in enumerate(COMBINED_ORGANISMS)}


#: The paper's two-column figure.
COMBINED_LAYOUT = CombinedLayout("", 14.2, COMBINED_PITCH, COMBINED_BAND_HALF, 0.54, 0.102)
#: A wider version for a one-column page: the same 2x3 grid on a wider canvas, with more space
#: between the organism groups (pitch) and between the three model sizes inside a group (offset).
EXPANDED_LAYOUT = CombinedLayout("_expanded", 20.0, 2.45, 1.08, 0.64, 0.072)

# Figure-local arm label. naming.ORGANISMS carries "RoBCoTDstl", which every generated
# table in tables/ also prints via \providecommand{\backdoorcot}; renaming it there would
# make the committed .gen.tex files disagree with the code until they are regenerated. The
# figure therefore overrides the label locally (Vasisht 2026-10-07).
FIGURE_LABELS = {"obfcot": "RobCoT"}


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

    # Mark-approved Jlbrk-only redraws (tables/approved_variants.json, scope "jlbrk") replace a seed's AutoDAN
    # fields exactly as the Table 2 render does, so the figure and the table show the same seeds.
    approved = tablefill._approved_variants("ours") if organism in naming.TABLE3_ORGANISMS else {}
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
        blob = tablefill._overlay_autodan(
            blob,
            organism,
            model,
            datasets,
            seed,
            path.name,
        )
        entry = approved.get(path.name)
        if entry is not None:
            if entry.get("scope") != "jlbrk":
                raise RuntimeError(f"approved variant {path.name}: only Jlbrk-only scope is supported here")
            blob = tablefill._apply_jlbrk_approval(
                blob, path.name, entry, organism, model, datasets, seed
            )
        blobs.append(blob)
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


def load_teammate_points(path: Path, organisms: tuple[str, ...] = ("robpwd", "robadapt")) -> list[Point]:
    """Per-seed AutoDAN values for the teammate's RobPWD/RobAdapt rows (``tables/attack_per_seed.json``).

    The file lists ``{organism_id, model, dataset, mean, se, seeds: [{value}]}`` with values in percent and
    seeds in roster order, so seed i maps to ``SEEDS[i]``. Only the Table 2 grid (Qwen2.5 sizes x six tasks)
    is read. Each entry's reported mean must match its seed values; a mismatch raises.
    """
    points: list[Point] = []
    for entry in json.loads(path.read_text()):
        organism, model, dataset = entry["organism_id"], entry["model"], entry["dataset"]
        if organism not in organisms or model not in MODELS or dataset not in TASKS:
            continue
        values = [seed["value"] for seed in entry["seeds"]]
        if len(values) != len(SEEDS) or any(value is None for value in values):
            raise RuntimeError(f"{path.name}: {organism}/{model}/{dataset} has {values}, not {len(SEEDS)} seeds")
        if abs(sum(values) / len(values) - entry["mean"]) > 0.051:
            raise RuntimeError(f"{path.name}: {organism}/{model}/{dataset} mean {entry['mean']} != seeds {values}")
        for seed, value in zip(SEEDS, values):
            points.append(Point("ours", organism, model, dataset, seed, value / 100.0, "teammate_json"))
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


def _boundary_rule(ax, x: float) -> None:
    """A full-height rule between two organisms, continuing a little below the x spine.

    Vasisht 2026-10-08: a rule confined to the axis margin was too quiet to tell the groups
    apart at column width. This one crosses the panel, but is set LIGHTER than the dashed
    prior/ours divider and only a little stronger than a gridline, so it divides without
    reading as a table grid (the earlier near-black version did).
    """
    ax.plot([x, x], [-0.03, 1.0], transform=ax.get_xaxis_transform(which="grid"),
            color="#c4c4c4", linewidth=1.8, solid_capstyle="butt", clip_on=False, zorder=0)


#: The combined figure's accuracies are strongly bimodal: prior work sits at 76-100% and the
#: proposed organisms at 0-30%, so a linear axis spends ~40% of every panel on an empty band
#: that no seed occupies, and squashes both clusters. The band is compressed (not deleted,
#: and the axis stays monotone and labelled) and the break is marked on the spine.
Y_BREAK_LO, Y_BREAK_HI, Y_BREAK_SQUEEZE = 32.0, 72.0, 0.10
COMBINED_YTICKS = (0, 10, 20, 30, 80, 90, 100)


def _broken_y_transforms():
    """Monotone piecewise-linear forward/inverse pair for ``ax.set_yscale('function', ...)``."""
    import numpy as np

    span = Y_BREAK_HI - Y_BREAK_LO
    kept = span * Y_BREAK_SQUEEZE

    def forward(y):
        y = np.asarray(y, dtype=float)
        middle = np.clip(y, Y_BREAK_LO, Y_BREAK_HI)
        return (
            np.minimum(y, Y_BREAK_LO)
            + (middle - Y_BREAK_LO) * Y_BREAK_SQUEEZE
            + np.maximum(y - Y_BREAK_HI, 0.0)
        )

    def inverse(v):
        v = np.asarray(v, dtype=float)
        middle = np.clip(v, Y_BREAK_LO, Y_BREAK_LO + kept)
        return (
            np.minimum(v, Y_BREAK_LO)
            + (middle - Y_BREAK_LO) / Y_BREAK_SQUEEZE
            + np.maximum(v - (Y_BREAK_LO + kept), 0.0)
        )

    return forward, inverse


def _mark_y_break(ax) -> None:
    """Cut the y spine at the compressed band and mark both cut ends.

    Vasisht 2026-10-08: the compressed band has to be unmissable, otherwise the axis reads as
    linear and the 30-to-80 jump looks like a plotting error. The spine is erased across the
    band (white overlay), a faint hatch fills the gap, and a large slanted mark sits at each
    cut end, extending a little into the panel.
    """
    forward, _ = _broken_y_transforms()
    lo, hi = ax.get_ylim()
    bottom, top = forward(lo), forward(hi)
    fractions = [(forward(level) - bottom) / (top - bottom) for level in (Y_BREAK_LO, Y_BREAK_HI)]

    # Erase the spine (and the panel's left edge) across the compressed band.
    ax.plot([0, 0], [fractions[0], fractions[1]], transform=ax.transAxes, color="white",
            linewidth=5.0, solid_capstyle="butt", clip_on=False, zorder=6)
    for fraction in fractions:
        ax.plot([-0.016, 0.016], [fraction - 0.016, fraction + 0.016], transform=ax.transAxes,
                color="#5f5f5f", linewidth=2.2, solid_capstyle="round", clip_on=False, zorder=7)


def _inpanel_model_legend(fig, axes, Patch, pitch: float = COMBINED_PITCH) -> None:
    """Model-size key inside the top-right panel instead of on a row under the figure.

    Vasisht 2026-10-07: the bottom label row cost vertical space that the boxes need. The
    'ours' half of every panel sits far below the prior-work boxes, so the band to the RIGHT
    of the prior/ours divider and above the proposed organisms is empty in all six datasets.
    The key is anchored there in DATA coordinates, so it can never drift back over a prior
    box the way an 'upper right' placement did once the organisms were spread out, and the
    type is stepped down only as far as needed to fit inside the panel.
    """
    from matplotlib.transforms import blended_transform_factory

    ax = axes[0][-1]
    handles = [
        Patch(facecolor=color, edgecolor=color, alpha=0.35, label=label)
        for label, color in MODEL_STYLE.values()
    ]
    transform = blended_transform_factory(ax.transData, ax.transAxes)
    # Right edge of the panel, with the hard constraint that the box must stay clear of the
    # prior-work organisms, i.e. start to the right of the prior/ours divider.
    limit = 2.5 * pitch + 0.25
    # The title is the part a reader has to parse ("what do these colors mean?"), the entries
    # are three short tokens, so the title is set ABOVE the entry size rather than below it,
    # which is what matplotlib's default legend.title_fontsize gives.
    for entry_size, title_size in ((21, 27), (19, 24), (17, 22), (15, 19)):
        legend = ax.legend(
            handles=handles, loc="upper right", title="Model", ncol=1,
            fontsize=entry_size, title_fontsize=title_size,
            bbox_to_anchor=(1.0, 0.995), bbox_transform=ax.transAxes,
            frameon=True, framealpha=0.95, edgecolor="#d9d9d9", fancybox=True,
            borderpad=0.4, labelspacing=0.3, handlelength=1.4, handletextpad=0.5,
            borderaxespad=0.0,
        )
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        if legend.get_window_extent(renderer).x0 >= transform.transform((limit, 0))[0]:
            return


def _bottom_row_label_and_legend(fig, axes, Patch) -> None:
    """Put the x-axis label and the model-size legend on ONE line under the tick labels.

    Both are positioned from measured text extents after a draw, so the row sits just below
    the lowest (rotated) tick label and stays centred under the axes whatever the fonts are.
    """
    import matplotlib

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    to_fig = fig.transFigure.inverted()
    tick_bottom = min(
        label.get_window_extent(renderer).transformed(to_fig).y0
        for ax in axes[-1]
        for label in ax.get_xticklabels()
    )
    handles = [
        Patch(facecolor=color, edgecolor=color, alpha=0.35, label=label)
        for label, color in MODEL_STYLE.values()
    ]
    xlabel = fig.text(0.0, 0.0, "Model Organisms", ha="left", va="center",
                      fontsize=matplotlib.rcParams["figure.labelsize"])
    legend = fig.legend(handles=handles, loc="center left", ncol=3, frameon=False,
                        borderaxespad=0.0, borderpad=0.0, columnspacing=1.2)
    entry_size = legend.get_texts()[0].get_fontsize()
    title = fig.text(0.0, 0.0, "Model Size:", ha="left", va="center", fontsize=entry_size)

    def width(artist) -> float:
        return artist.get_window_extent(renderer).transformed(to_fig).width

    height = xlabel.get_window_extent(renderer).transformed(to_fig).height
    gap, title_gap = 0.035, 0.006
    total = width(xlabel) + gap + width(title) + title_gap + width(legend)
    axes_left = min(ax.get_position().x0 for ax in axes.flat)
    axes_right = max(ax.get_position().x1 for ax in axes.flat)
    x = 0.5 * (axes_left + axes_right) - 0.5 * total
    y = tick_bottom - 0.012 - 0.5 * height
    xlabel.set_position((x, y))
    x += width(xlabel) + gap
    title.set_position((x, y))
    x += width(title) + title_gap
    legend.set_bbox_to_anchor((x, y), transform=fig.transFigure)


def render_boxplot(
    table: str,
    points: list[Point],
    out_dir: Path,
    include_ours: tuple[str, ...] = ("obfcot",),
    layout: CombinedLayout = COMBINED_LAYOUT,
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
    if is_combined:
        # Per-figure sizing overlay (precedent: whitebox_curves.render_composite). The
        # combined boxplot is reduced hard by \includegraphics in a two-column layout, so
        # every text element is set well above the shared house sizes.
        plt.rcParams.update({
            "axes.titlesize": 30,
            "xtick.labelsize": 23,
            "ytick.labelsize": 24,
            "legend.fontsize": 21,
            "figure.labelsize": 28,
        })
    fig_width = layout.fig_width if is_combined else 15.6
    # Taller panels for the same page width: at a fixed \textwidth the aspect ratio is what
    # decides how large a box reads on the page, so the combined grid is less wide than tall.
    fig, axes = plt.subplots(2, 3, figsize=(fig_width, 9.2), sharex=True, sharey=True)
    x_positions = (
        layout.x_positions() if is_combined else dict(zip(organisms, range(len(organisms))))
    )
    x_centres = [x_positions[organism] for organism in organisms]
    model_offset = layout.model_offset if is_combined else 0.23
    offsets = dict(zip(MODELS, (-model_offset, 0.0, model_offset)))
    seed_jitter = dict(zip(SEEDS, (-0.075, 0.0, 0.075) if is_combined else (-0.035, 0.0, 0.035)))

    for ax, dataset in zip(axes.flat, TASKS):
        if is_combined:
            # Three cues, in increasing strength: whitespace between groups (several times
            # the gap inside a group), a light full-height rule at every organism boundary,
            # and the dashed divider for the one boundary that is not merely a grouping,
            # prior vs ours. The rule is deliberately lighter than the divider so the two
            # do not compete.
            for index in range(1, len(organisms)):
                _boundary_rule(ax, (index - 0.5) * layout.pitch)
            ax.axvline(2.5 * layout.pitch, color="#6f6f6f", linewidth=2.2,
                       linestyle=(0, (6, 4)), zorder=1)

        for organism in organisms:
            organism_position = x_positions[organism]
            if organism not in included:
                ax.text(
                    organism_position,
                    3.0,
                    "Pending",
                    ha="center",
                    va="bottom",
                    color="#888888",
                    fontsize=17 if is_combined else plot_style.ANNOT_SIZE - 2,
                )
                continue
            for model in MODELS:
                cell = sorted(
                    by_cell.get((organism, model, dataset), []),
                    key=lambda point: point.seed,
                )
                label, color = MODEL_STYLE[model]
                position = organism_position + offsets[model]
                if not cell:
                    continue
                values = [100.0 * point.accuracy for point in cell]
                ax.boxplot(
                    [values],
                    positions=[position],
                    widths=0.46 if is_combined else 0.18,
                    patch_artist=True,
                    manage_ticks=False,
                    showfliers=True,
                    boxprops={
                        "facecolor": color,
                        "edgecolor": color,
                        "alpha": 0.28,
                        "linewidth": 2.4 if is_combined else 1.7,
                    },
                    medianprops={"color": color, "linewidth": 3.4 if is_combined else 2.3},
                    whiskerprops={"color": color, "linewidth": 2.0 if is_combined else 1.4},
                    capprops={"color": color, "linewidth": 2.0 if is_combined else 1.4},
                    flierprops={
                        "marker": "o",
                        "markerfacecolor": color,
                        "markeredgecolor": "white",
                        "markersize": 7.5 if is_combined else 5,
                        "alpha": 0.9,
                    },
                )
                for point in cell:
                    ax.scatter(
                        position + seed_jitter[point.seed],
                        100.0 * point.accuracy,
                        s=58 if is_combined else 31,
                        color=color,
                        edgecolor="white",
                        linewidth=0.65,
                        zorder=4,
                    )

        ax.set_title(naming.TASKS[dataset], pad=10 if is_combined else 8)
        if is_combined:
            ax.set_xlim(-layout.band_half - 0.04,
                        x_centres[-1] + layout.band_half + 0.04)
        else:
            ax.set_xlim(-0.55, len(organisms) - 0.45)
        if is_combined:
            ax.set_yscale("function", functions=_broken_y_transforms())
            ax.set_yticks(list(COMBINED_YTICKS))
        else:
            ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_ylim(-1.5, 103.0)
        ax.set_xticks(x_centres)
        ax.set_xticklabels(
            [
                FIGURE_LABELS.get(org, naming.ORGANISMS[org].paper) if is_combined
                else naming.ORGANISMS[org].paper
                for org in organisms
            ],
            fontsize=(
                23
                if is_combined
                else (15 if table == "ours" else plot_style.TICK_SIZE)
            ),
            rotation=30 if is_combined else 0,
            ha="right" if is_combined else "center",
            rotation_mode="anchor" if is_combined else None,
        )
        # Vasisht 2026-10-08: no horizontal gridlines on the combined figure. The y ticks
        # and the boxes' own medians carry the reading, and with the compressed band the
        # rules only added clutter.
        if not is_combined:
            ax.grid(axis="y", alpha=0.25, linewidth=0.8)
        ax.set_axisbelow(True)
        plot_style.despine(ax)
        if is_combined:
            _mark_y_break(ax)

    fig.subplots_adjust(
        left=layout.left if is_combined else 0.065,
        right=0.995,
        bottom=0.105 if is_combined else 0.12,
        top=0.95,
        wspace=0.06,
        hspace=0.24 if is_combined else 0.22,
    )
    fig.supylabel("Elicitation Accuracy (%)", x=0.012)
    if is_combined:
        _inpanel_model_legend(fig, axes, Patch, layout.pitch)
    else:
        _bottom_row_label_and_legend(fig, axes, Patch)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"jailbreak_boxplot_{table}{layout.suffix if is_combined else ''}"
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
        default=ROOT / "tables" / "jailbreak" / "jailbreak_boxplot_prior.csv",
        help="Audited prior-work seed CSV used by --table combined.",
    )
    parser.add_argument(
        "--ours-csv",
        type=Path,
        default=ROOT / "tables" / "jailbreak" / "jailbreak_boxplot_ours.csv",
        help="Audited proposed-organism seed CSV used by --table combined.",
    )
    parser.add_argument(
        "--teammate-json",
        type=Path,
        default=None,
        help="Teammate per-seed RobPWD/RobAdapt AutoDAN file (tables/attack_per_seed.json); --table ours only.",
    )
    parser.add_argument(
        "--expanded",
        action="store_true",
        help="--table combined only: render the wider one-column version "
        "(jailbreak_boxplot_combined_expanded.*) instead of the two-column one.",
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tables" / "jailbreak")
    args = parser.parse_args()
    if args.expanded and args.table != "combined":
        parser.error("--expanded applies to --table combined")

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
        if args.teammate_json is not None:
            if args.table != "ours":
                parser.error("--teammate-json applies to --table ours")
            points += load_teammate_points(args.teammate_json)
            args.include_ours = tuple(
                organism
                for organism in OURS_ORGANISMS
                if organism in set(args.include_ours) | {point.organism for point in points}
            )
    csv_path = args.out_dir / f"jailbreak_boxplot_{args.table}.csv"
    write_csv(points, csv_path)
    layout = EXPANDED_LAYOUT if args.expanded else COMBINED_LAYOUT
    paths = render_boxplot(args.table, points, args.out_dir, tuple(args.include_ours), layout)
    print(f"[jailbreak-boxplots] wrote {csv_path} ({len(points)} seed values)")
    for path in paths:
        print(f"[jailbreak-boxplots] wrote {path}")


if __name__ == "__main__":
    main()
