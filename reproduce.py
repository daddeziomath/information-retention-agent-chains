"""Reproduce the original-fact retention results from archived automated scores."""

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import gzip
from pathlib import Path

import numpy as np


DEFAULT_SCORES = Path(__file__).resolve().parent / "data" / "results_main.csv.gz"
CONDITIONS = ("A", "B")
ARMS = ("baseline", "keep")
HOPS = range(1, 11)
SEED = 20261109
BOOTSTRAP_DRAWS = 20_000


@dataclass
class Estimate:
    kept: np.ndarray
    total: np.ndarray
    rate: np.ndarray
    rate_interval: np.ndarray
    effect_interval: np.ndarray


def estimate_retention(path: Path) -> dict[tuple[int, str], Estimate]:
    """Pool facts within each arm and resample their source documents in pairs."""
    kept = Counter()
    total = Counter()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        scores = csv.DictReader(source)
        required = {"origin", "k", "condition", "arm", "item_id", "status_v3"}
        missing = required - set(scores.fieldnames or [])
        if missing:
            raise ValueError(f"Missing score columns: {', '.join(sorted(missing))}")
        for row in scores:
            hop = int(row["k"])
            if row["origin"] != "original" or hop not in HOPS:
                continue
            condition, arm = row["condition"], row["arm"]
            if condition not in CONDITIONS or arm not in ARMS:
                raise ValueError(f"Unexpected condition or arm: {condition}, {arm}")
            cell = hop, condition, row["item_id"], arm
            total[cell] += 1
            kept[cell] += row["status_v3"] == "kept"

    documents = sorted({cell[2] for cell in total})
    if not documents:
        raise ValueError("No scored original facts were found at hops 1–10.")

    # A draw contains as many documents as the corpus, sampled with replacement.
    # Each weight is shared by all hops, conditions and arms of that document.
    weights = np.random.default_rng(SEED).multinomial(
        len(documents),
        np.full(len(documents), 1 / len(documents)),
        size=BOOTSTRAP_DRAWS,
    )
    estimates = {}
    for hop in HOPS:
        for condition in CONDITIONS:
            cells = [[(hop, condition, doc, arm) for arm in ARMS] for doc in documents]
            kept_by_document = np.array([[kept[cell] for cell in row] for row in cells])
            total_by_document = np.array([[total[cell] for cell in row] for row in cells])
            if (total_by_document == 0).any():
                raise ValueError(f"Incomplete document pairs at hop {hop}, condition {condition}.")

            point_kept = kept_by_document.sum(axis=0)
            point_total = total_by_document.sum(axis=0)
            boot_rates = (weights @ kept_by_document) / (weights @ total_by_document)
            estimates[hop, condition] = Estimate(
                kept=point_kept,
                total=point_total,
                rate=point_kept / point_total,
                rate_interval=np.quantile(boot_rates, (0.025, 0.975), axis=0),
                effect_interval=np.quantile(boot_rates[:, 1] - boot_rates[:, 0], (0.025, 0.975)),
            )
    return estimates


def save_figure(estimates: dict[tuple[int, str], Estimate], path: Path) -> None:
    """Regenerate the retention curves and pointwise bootstrap bands in Figure 1."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.8), sharey=True)
    titles = ("A  Original source only at first relay", "B  Original source at every relay")
    for axis, condition, title in zip(axes, CONDITIONS, titles):
        series = [estimates[hop, condition] for hop in HOPS]
        for index, (label, color) in enumerate(zip(("Baseline", "Keep"), ("#205d87", "#c25436"))):
            rates = [100 * value.rate[index] for value in series]
            low = [100 * value.rate_interval[0, index] for value in series]
            high = [100 * value.rate_interval[1, index] for value in series]
            axis.plot(HOPS, rates, label=label, color=color, marker="o", markersize=3)
            axis.fill_between(HOPS, low, high, color=color, alpha=0.15, linewidth=0)
        axis.set_title(title, fontsize=10)
        axis.set_xlabel("Relay hop")
        axis.set_xticks([1, 3, 5, 7, 10])
        axis.set_xlim(0.8, 10.2)
        axis.set_ylim(0, 72)
        axis.grid(axis="y", alpha=0.3)
        axis.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Original facts scored correct (%)")
    axes[0].legend(frameon=False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"pdf.fonttype": 42}):
        fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES, help="Path to the scored CSV.gz.")
    parser.add_argument("--figure", type=Path, help="Optional output path for the retention plot.")
    args = parser.parse_args()

    estimates = estimate_retention(args.scores)
    print("Original facts scored correct at hop 10")
    print("Condition  Baseline                 Keep                     Keep − baseline (95% CI)")
    for condition in CONDITIONS:
        estimate = estimates[10, condition]
        baseline, keep = (
            f"{n:,}/{denom:,} = {rate:.6f}"
            for n, denom, rate in zip(estimate.kept, estimate.total, estimate.rate)
        )
        difference = estimate.rate[1] - estimate.rate[0]
        low, high = estimate.effect_interval
        print(f"{condition:9}  {baseline:23}  {keep:23}  {difference:+.6f} [{low:+.6f}, {high:+.6f}]")
    print(
        f"Percentile intervals from {BOOTSTRAP_DRAWS:,} paired document bootstrap draws "
        f"(seed {SEED}), conditional on the archived notes and scores."
    )
    if args.figure:
        save_figure(estimates, args.figure)
        print(f"Figure saved to {args.figure}")


if __name__ == "__main__":
    main()
