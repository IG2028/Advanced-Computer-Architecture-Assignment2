#!/usr/bin/env python3
"""
Plot the PA2 metrics from results/summary.csv (run parse_results.py first).

Writes results/plots/<task>_<metric>.png

Conventions (the same in every chart, following the example plots in the
problem statement):
  - The baseline (no prefetcher, LRU L2) is never a bar and never a legend
    entry. It is a dotted reference line labelled directly on the chart:
      speedup        -> one dotted line at 1.0
      IPC / MPKI     -> a dotted tick at the baseline's value over each trace
      coverage       -> a dotted line at 0 (the baseline's coverage by definition)
      accuracy       -> nothing (the baseline issues no prefetches)
  - Every other configuration is a bar with a fixed colour, so a config looks
    the same in every chart. The legend lists only those bars.
  - The last group is "geomean" for IPC and speedup and "mean" otherwise.
  - A config that has no value for a metric (e.g. accuracy without a
    prefetcher, I-RRIP fraction for a static policy) is left out of that chart
    rather than drawn as a zero bar.
  - Threshold-sweep line charts draw the comparison policies (rrip, irrip and
    the default dyn_irrip) as dotted reference lines too.

Usage
  python3 scripts/plot_results.py [--results results] [--baseline baseline]
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
except ImportError:
    sys.exit("matplotlib is needed: pip install matplotlib")

# Fixed categorical order (validated colour-blind-safe palette).
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
# palette slot per config, chosen so configs that share a chart get well-separated hues
CONFIG_SLOT = {"spp": 1, "dyn_irrip": 2, "spp_pf_dyn_irrip": 3, "spp_dyn_irrip": 4,
               "ip_stride": 5, "rrip": 6, "irrip": 7}
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"
REF = "#0b0b0b"          # reference (baseline) lines: dark ink, dotted

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
# what the baseline reference looks like for each metric
#   "value": per-trace baseline value; "one": constant 1.0; "zero": constant 0; None: no reference
BASELINE_REF = {
    "ipc": "value", "speedup": "one", "l1d_load_mpki": "value", "l1d_pf_accuracy": None,
    "coverage": "zero", "l2c_load_mpki": "value", "l2c_instr_load_mpki": "value",
    "l2c_data_load_mpki": "value", "dirrip_irrip_fraction": None,
}
BASELINE_LABEL = "baseline (no prefetch, LRU)"


def color_for(config, bar_configs):
    if config in CONFIG_SLOT:
        return PALETTE[CONFIG_SLOT[config]]
    return PALETTE[bar_configs.index(config) % len(PALETTE)]   # sweep points: their own fixed order


def style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def ref_note(ax, text):
    # direct label for a full-width reference line, top right above the plot area (never in the legend)
    ax.text(1.0, 1.01, "\u00b7\u00b7\u00b7\u00b7 " + text, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=7, color=MUTED)


def load(results):
    path = os.path.join(results, "summary.csv")
    if not os.path.exists(path):
        sys.exit("%s not found; run scripts/parse_results.py first" % path)
    data = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            data.setdefault(row["config"], {})[row["trace"]] = {k: fnum(v) for k, v in row.items() if k not in ("config", "trace")}
    return data


def agg_for(metric):
    return geomean if metric in GEOMEAN_METRICS else amean


def config_mean(data, config, metric):
    return agg_for(metric)([data[config][t].get(metric, float("nan")) for t in data[config]])


def has_metric(data, config, metric):
    return any(not math.isnan(r.get(metric, float("nan"))) for r in data[config].values())


def bar_chart(data, configs, metric, title, out, baseline):
    bars = [c for c in configs if c != baseline and has_metric(data, c, metric)]
    if not bars:
        return False
    traces = sorted({t for c in bars for t in data[c]})
    mean_label = "geomean" if metric in GEOMEAN_METRICS else "mean"
    groups = traces + [mean_label]
    n = len(bars)
    width = 0.8 / n
    fig, ax = plt.subplots(figsize=(max(6.0, 1.1 * len(groups) * max(1.0, n / 2.5)), 3.6))

    # Speedups differ from 1.0 by a few percent, so speedup bars grow up or down
    # from the 1.0 baseline line and the axis is zoomed to the data; every other
    # metric is drawn from zero.
    from_one = metric == "speedup"
    all_vals = []
    for i, c in enumerate(bars):
        vals = [data[c].get(t, {}).get(metric, float("nan")) for t in traces] + [config_mean(data, c, metric)]
        all_vals += [v for v in vals if not math.isnan(v)]
        xs = [g + (i - (n - 1) / 2) * width for g in range(len(groups))]
        if from_one:
            ax.bar(xs, [0 if math.isnan(v) else v - 1.0 for v in vals], bottom=1.0, width=width * 0.9,
                   color=color_for(c, bars), label=c, edgecolor="white", linewidth=0.5)
        else:
            ax.bar(xs, [0 if math.isnan(v) else v for v in vals], width=width * 0.9,
                   color=color_for(c, bars), label=c, edgecolor="white", linewidth=0.5)
    if from_one and all_vals:
        lo, hi = min(all_vals + [1.0]), max(all_vals + [1.0])
        pad = max(0.002, 0.15 * (hi - lo))
        ax.set_ylim(lo - pad, hi + pad)

    # ---- baseline as a dotted reference, labelled directly (never in the legend) ----
    ref = BASELINE_REF.get(metric)
    have_base = baseline in data
    if ref == "one":
        ax.axhline(1.0, color=REF, linewidth=1.0, linestyle=":")
        ref_note(ax, BASELINE_LABEL + " = 1.0")
    elif ref == "zero":
        ax.axhline(0.0, color=REF, linewidth=1.0, linestyle=":")
        ref_note(ax, BASELINE_LABEL + " = 0")
    elif ref == "value" and have_base:
        base_vals = [data[baseline].get(t, {}).get(metric, float("nan")) for t in traces] + [config_mean(data, baseline, metric)]
        half = 0.45
        for g, v in enumerate(base_vals):
            if math.isnan(v):
                continue
            ax.hlines(v, g - half, g + half, colors=REF, linewidth=1.2, linestyles=":")
        ref_note(ax, BASELINE_LABEL + " (value per trace)")

    if metric == "dirrip_irrip_fraction":
        ax.set_ylim(0, 1.05)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups)
    ax.set_ylabel(LABELS.get(metric, metric), color=TEXT, fontsize=9)
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")
    style(ax)
    ax.legend(fontsize=7, frameon=False, ncol=min(n, 4), loc="upper left", bbox_to_anchor=(0, -0.12))
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def sweep_chart(data, configs, prefix, metric, out, baseline, refs):
    xs, ys = [], []
    for c in configs:
        xs.append(fnum(c[len(prefix):]))
        ys.append(config_mean(data, c, metric))
    if all(math.isnan(y) for y in ys):
        return False
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    ax.plot(xs, ys, color=PALETTE[0], linewidth=2, marker="o", markersize=6, zorder=3)
    for x, y in zip(xs, ys):
        if not math.isnan(y):
            ax.annotate("%.4f" % y, (x, y), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=7, color=MUTED)

    # reference policies and the baseline as dotted lines with direct labels
    lines = []
    if BASELINE_REF.get(metric) == "one":
        lines.append((BASELINE_LABEL, 1.0))
    elif BASELINE_REF.get(metric) == "value" and baseline in data:
        lines.append((BASELINE_LABEL, config_mean(data, baseline, metric)))
    for r in refs:
        if r in data and has_metric(data, r, metric):
            lines.append((r, config_mean(data, r, metric)))
    for name, v in lines:
        if math.isnan(v):
            continue
        ax.axhline(v, color=REF if name == BASELINE_LABEL else color_for(name, []), linewidth=1.0, linestyle=":")
        ax.annotate(name, xy=(1.0, v), xycoords=("axes fraction", "data"), xytext=(3, 0), textcoords="offset points",
                    ha="left", va="center", fontsize=7, color=MUTED)

    ax.set_xlabel("Decision threshold", color=TEXT, fontsize=9)
    ax.set_ylabel(("geomean " if metric in GEOMEAN_METRICS else "mean ") + LABELS.get(metric, metric), color=TEXT, fontsize=9)
    ax.set_title("Dynamic I-RRIP threshold sensitivity", color=TEXT, fontsize=10, loc="left")
    style(ax)
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--baseline", default="baseline")
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
        sweep = spec[0].startswith("SWEEP:")
        if sweep:
            tag += "_sweep"
        for metric in metrics:
            out = os.path.join(outdir, "%s_%s.png" % (tag, metric))
            if bar_chart(data, configs, metric, title, out, args.baseline):
                print("wrote", out)
            if sweep:
                prefix = spec[0][len("SWEEP:"):]
                out = os.path.join(outdir, "%s_%s_line.png" % (tag, metric))
                if sweep_chart(data, configs, prefix, metric, out, args.baseline, ["rrip", "irrip", "dyn_irrip"]):
                    print("wrote", out)


if __name__ == "__main__":
    main()
