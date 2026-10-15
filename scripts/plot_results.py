#!/usr/bin/env python3
"""
Plot the PA2 metrics from results/summary.csv (run parse_results.py first).

Writes results/plots/<task>_<metric>.png:
  - one grouped bar chart per (task, metric): x = traces + a mean group,
    one bar per configuration, the same colour for a configuration in every chart
  - for the Task 2B threshold sweep, one line chart per metric of the mean
    value against the threshold

Usage
  python3 scripts/plot_results.py [--results results]
Needs matplotlib (pip install matplotlib).
"""
import argparse
import csv
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_results import TASKS, GEOMEAN_METRICS, expand_configs, geomean, amean, fnum  # noqa: E402

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker
except ImportError:
    sys.exit("matplotlib is needed: pip install matplotlib")

# Fixed categorical order (validated colour-blind-safe palette); a config keeps its colour everywhere.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
# palette slot per config, chosen so configs that share a chart get well-separated hues
CONFIG_SLOT = {"baseline": 0, "spp": 1, "dyn_irrip": 2, "spp_pf_dyn_irrip": 3, "spp_dyn_irrip": 4,
               "ip_stride": 5, "rrip": 6, "irrip": 7}
BASELINE = "baseline"
# Which configurations each chart shows (only bars that mean something get a
# legend entry):
#  - coverage / accuracy: only configs with an L1D prefetcher. The baseline and
#    dyn_irrip issue no prefetches: accuracy is undefined, coverage is ~0.
#  - speedup: no baseline bar (it is 1 by definition); the baseline is the
#    dotted line at 1.0 instead, and bars grow up/down from that line on a
#    zoomed axis, because every speedup is within a few percent of 1.
#  - I-RRIP fraction: only the dynamic policy has one.
PREFETCH_METRICS = {"coverage", "l1d_pf_accuracy"}
ONLY_IF_PRESENT = {"dirrip_irrip_fraction"}
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"

LABELS = {
    "ipc": "IPC",
    "speedup": "Speedup over baseline",
    "l1d_load_mpki": "L1D load MPKI",
    "l1d_pf_accuracy": "L1D prefetch accuracy (%)",
    "coverage": "L1D coverage (%)",
    "l2c_load_mpki": "L2C load MPKI",
    "l2c_instr_load_mpki": "L2C instruction load MPKI",
    "l2c_data_load_mpki": "L2C data load MPKI",
    "dirrip_irrip_fraction": "Fraction of epochs in I-RRIP",
}


def color_for(config, configs_in_chart):
    if config in CONFIG_SLOT:
        return PALETTE[CONFIG_SLOT[config]]
    # sweep points: assign in their own fixed order
    return PALETTE[configs_in_chart.index(config) % len(PALETTE)]


def style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def load(results):
    path = os.path.join(results, "summary.csv")
    if not os.path.exists(path):
        sys.exit("%s not found; run scripts/parse_results.py first" % path)
    data = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            data.setdefault(row["config"], {})[row["trace"]] = {k: fnum(v) for k, v in row.items() if k not in ("config", "trace")}
    return data


def has_metric(data, config, metric):
    return any(not math.isnan(r.get(metric, float("nan"))) for r in data[config].values())


def bar_chart(data, configs, metric, title, out):
    if metric in PREFETCH_METRICS:
        configs = [c for c in configs if has_metric(data, c, "l1d_pf_accuracy")]
    elif metric in ONLY_IF_PRESENT:
        configs = [c for c in configs if has_metric(data, c, metric)]
    elif metric == "speedup":
        configs = [c for c in configs if c != BASELINE]
    if not configs:
        return False
    traces = sorted({t for c in configs for t in data[c]})
    agg = geomean if metric in GEOMEAN_METRICS else amean
    groups = traces + ["mean"]
    n = len(configs)
    width = 0.8 / n
    from_one = metric == "speedup"
    all_vals = []
    fig, ax = plt.subplots(figsize=(max(6, 0.6 * len(groups) * max(1, n / 2)), 3.6))
    for i, c in enumerate(configs):
        vals = [data[c].get(t, {}).get(metric, float("nan")) for t in traces]
        vals.append(agg([data[c][t].get(metric, float("nan")) for t in data[c]]))
        xs = [g + (i - (n - 1) / 2) * width for g in range(len(groups))]
        all_vals += [v for v in vals if not math.isnan(v)]
        if from_one:   # bar from the 1.0 line to the value (up = faster, down = slower)
            ax.bar(xs, [0 if math.isnan(v) else v - 1.0 for v in vals], bottom=1.0, width=width * 0.9,
                   color=color_for(c, configs), label=c, edgecolor="white", linewidth=0.5)
        else:
            ax.bar(xs, [0 if math.isnan(v) else v for v in vals], width=width * 0.9,
                   color=color_for(c, configs), label=c, edgecolor="white", linewidth=0.5)
    if from_one:
        ax.axhline(1.0, color=TEXT, linewidth=1.0, linestyle=":")   # baseline = 1.0
        lo, hi = min(all_vals + [1.0]), max(all_vals + [1.0])
        pad = max(0.002, 0.15 * (hi - lo))
        ax.set_ylim(lo - pad, hi + pad)                             # finer scale around 1
        ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.4f"))
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups, rotation=35, ha="right")
    ax.set_ylabel(LABELS.get(metric, metric), color=TEXT, fontsize=9)
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")
    style(ax)
    ax.legend(fontsize=7, frameon=False, ncol=min(n, 4), loc="upper left", bbox_to_anchor=(0, -0.28))
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def sweep_chart(data, configs, prefix, metric, out):
    xs, ys = [], []
    agg = geomean if metric in GEOMEAN_METRICS else amean
    for c in configs:
        xs.append(fnum(c[len(prefix):]))
        ys.append(agg([data[c][t].get(metric, float("nan")) for t in data[c]]))
    fig, ax = plt.subplots(figsize=(5, 3.2))
    ax.plot(xs, ys, color=PALETTE[0], linewidth=2, marker="o", markersize=6)
    if metric == "speedup":
        ax.axhline(1.0, color=TEXT, linewidth=1.0, linestyle=":")   # baseline = 1.0
        lo, hi = min([y for y in ys if not math.isnan(y)] + [1.0]), max([y for y in ys if not math.isnan(y)] + [1.0])
        pad = max(0.002, 0.15 * (hi - lo))
        ax.set_ylim(lo - pad, hi + pad)
    for x, y in zip(xs, ys):
        if not math.isnan(y):
            ax.annotate("%.4f" % y if metric == "speedup" else "%.3f" % y, (x, y), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=7, color=MUTED)
    ax.set_xlabel("Decision threshold", color=TEXT, fontsize=9)
    ax.set_ylabel(("mean " if metric not in GEOMEAN_METRICS else "geomean ") + LABELS.get(metric, metric), color=TEXT, fontsize=9)
    ax.set_title("Dynamic I-RRIP threshold sensitivity", color=TEXT, fontsize=10, loc="left")
    style(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    args = ap.parse_args()

    data = load(args.results)
    outdir = os.path.join(args.results, "plots")
    os.makedirs(outdir, exist_ok=True)
    all_configs = sorted(data)

    for title, spec, metrics in TASKS:
        configs = expand_configs(spec, all_configs)
        if not configs:
            continue
        tag = title.split(":")[0].lower().replace(" ", "")
        if spec[0].startswith("SWEEP:"):
            tag += "_sweep"
        for metric in metrics:
            out = os.path.join(outdir, "%s_%s.png" % (tag, metric))
            if bar_chart(data, configs, metric, title, out):
                print("wrote", out)
            if spec[0].startswith("SWEEP:"):
                prefix = spec[0][len("SWEEP:"):]
                out = os.path.join(outdir, "%s_%s_line.png" % (tag, metric))
                sweep_chart(data, configs, prefix, metric, out)
                print("wrote", out)


if __name__ == "__main__":
    main()
