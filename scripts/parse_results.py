#!/usr/bin/env python3
"""
Collect the PA2 metrics from ChampSim output files.

Reads   results/<config>/<trace>.txt   (written by scripts/run_all.sh)
Writes  results/summary.csv            one row per (config, trace), raw + derived metrics
        results/tables.md              one table per task (the report tables)

Only the "Region of Interest Statistics" part of each file is used, i.e. the
25M simulated instructions after warmup.

Derived metrics
  speedup   = IPC(config) / IPC(baseline config)                     (spec definition)
  coverage  = (L1D load misses(baseline) - L1D load misses(config))
              / L1D load misses(baseline)                            (spec definition)
  accuracy  = L1D useful prefetches / L1D prefetches issued to lower level
              (= the "ACCURACY" ChampSim prints, recomputed to avoid nan)

Usage
  python3 scripts/parse_results.py [--results results] [--baseline baseline]

Standard library only.
"""
import argparse
import csv
import math
import os
import re
import sys

NUM = r"(-?nan|-?inf|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"

ROI_MARKER = "Region of Interest Statistics"

PATTERNS = {
    "ipc": re.compile(r"^CPU 0 cumulative IPC:\s*" + NUM + r"\s+instructions:\s*(\d+)\s+cycles:\s*(\d+)"),
    "load": re.compile(r"^(L1D|L1I|L2C|LLC) LOAD\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+).*MPKI:\s*" + NUM),
    "total": re.compile(r"^(L1D|L1I|L2C|LLC) TOTAL\s+ACCESS:\s*(\d+)\s+HIT:\s*(\d+)\s+MISS:\s*(\d+).*MPKI:\s*" + NUM),
    "l2_data": re.compile(r"^L2C DATA LOAD MPKI:\s*" + NUM),
    "l2_instr": re.compile(r"^L2C INSTRUCTION LOAD MPKI:\s*" + NUM),
    "pf_req": re.compile(r"^(L1D|L2C) PREFETCH\s+REQUESTED:\s*(\d+)\s+ISSUED:\s*(\d+)\s+USEFUL:\s*(\d+)\s+USELESS:\s*(\d+)"),
    "pf_acc": re.compile(r"^(L1D|L2C) USEFUL LOAD PREFETCHES:\s*(\d+)\s+PREFETCH ISSUED TO LOWER LEVEL:\s*(\d+)"),
    "pf_late": re.compile(r"^(L1D|L2C) TIMELY PREFETCHES:\s*(\d+)\s+LATE PREFETCHES:\s*(\d+)\s+DROPPED PREFETCHES:\s*(\d+)"),
    # custom lines printed by the PA2 code (anywhere in the file; they are ROI-only by construction)
    "spp_acc": re.compile(r"^SPP filter accuracy:\s*" + NUM + r"\s*%\s*uniform:\s*" + NUM + r"\s*%\s*pattern:\s*" + NUM),
    "spp_storage": re.compile(r"^SPP storage: TOTAL\s*(\d+)\s*bits"),
    "spp_degree": re.compile(r"^SPP final degree:\s*(\d+)"),
    "dirrip": re.compile(r"^DIRRIP epochs:\s*(\d+)\s+epochs_irrip:\s*(\d+)\s+irrip_fraction:\s*" + NUM),
    "dirrip_ratio": re.compile(r"^DIRRIP last_ratio:\s*" + NUM + r"\s+mean_ratio:\s*" + NUM),
}


def fnum(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return float("nan")
    return v


def parse_file(path):
    """Return a dict of raw metrics for one ChampSim output, or None if unfinished."""
    with open(path, errors="replace") as f:
        lines = f.read().splitlines()
    if not any("ChampSim completed all CPUs" in l for l in lines):
        return None

    try:
        roi = next(i for i, l in enumerate(lines) if ROI_MARKER in l)
    except StopIteration:
        return None

    r = {}
    for i, line in enumerate(lines):
        line = line.strip()
        in_roi = i > roi

        m = PATTERNS["ipc"].match(line)
        if m and in_roi:
            r["ipc"] = fnum(m.group(1))
            r["instructions"] = int(m.group(2))
            r["cycles"] = int(m.group(3))
            continue
        m = PATTERNS["load"].match(line)
        if m and in_roi:
            c = m.group(1).lower()
            r[c + "_load_access"] = int(m.group(2))
            r[c + "_load_miss"] = int(m.group(4))
            r[c + "_load_mpki"] = fnum(m.group(5))
            continue
        m = PATTERNS["total"].match(line)
        if m and in_roi:
            c = m.group(1).lower()
            r[c + "_total_miss"] = int(m.group(4))
            r[c + "_total_mpki"] = fnum(m.group(5))
            continue
        m = PATTERNS["l2_data"].match(line)
        if m and in_roi:
            r["l2c_data_load_mpki"] = fnum(m.group(1))
            continue
        m = PATTERNS["l2_instr"].match(line)
        if m and in_roi:
            r["l2c_instr_load_mpki"] = fnum(m.group(1))
            continue
        m = PATTERNS["pf_req"].match(line)
        if m and in_roi:
            c = m.group(1).lower()
            r[c + "_pf_requested"] = int(m.group(2))
            r[c + "_pf_issued"] = int(m.group(3))
            r[c + "_pf_useful"] = int(m.group(4))
            r[c + "_pf_useless"] = int(m.group(5))
            continue
        m = PATTERNS["pf_acc"].match(line)
        if m and in_roi:
            c = m.group(1).lower()
            r[c + "_pf_to_lower"] = int(m.group(3))
            continue
        m = PATTERNS["pf_late"].match(line)
        if m and in_roi:
            c = m.group(1).lower()
            r[c + "_pf_late"] = int(m.group(3))
            continue
        m = PATTERNS["spp_acc"].match(line)
        if m:
            r["spp_filter_acc"] = fnum(m.group(1))
            continue
        m = PATTERNS["spp_storage"].match(line)
        if m:
            r["spp_storage_bits"] = int(m.group(1))
            continue
        m = PATTERNS["spp_degree"].match(line)
        if m:
            r["spp_final_degree"] = int(m.group(1))
            continue
        m = PATTERNS["dirrip"].match(line)
        if m:
            r["dirrip_irrip_fraction"] = fnum(m.group(3))
            continue
        m = PATTERNS["dirrip_ratio"].match(line)
        if m:
            r["dirrip_mean_ratio"] = fnum(m.group(2))
            continue

    useful = r.get("l1d_pf_useful", 0)
    lower = r.get("l1d_pf_to_lower", 0)
    r["l1d_pf_accuracy"] = 100.0 * useful / lower if lower else float("nan")
    return r


def geomean(xs):
    xs = [x for x in xs if isinstance(x, float) and x > 0 and not math.isnan(x)]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def amean(xs):
    xs = [x for x in xs if isinstance(x, (int, float)) and not math.isnan(x)]
    return sum(xs) / len(xs) if xs else float("nan")


# Which configs and metrics go into each report table.
# A config that was not run is simply left out.
TASKS = [
    ("Task 1B: SPP at L1D",
     ["baseline", "ip_stride", "spp"],
     ["ipc", "speedup", "l1d_load_mpki", "l1d_pf_accuracy", "coverage"]),
    ("Task 2A: I-RRIP at L2C",
     ["baseline", "rrip", "irrip"],
     ["ipc", "speedup", "l2c_load_mpki", "l2c_instr_load_mpki", "l2c_data_load_mpki"]),
    ("Task 2B: Dynamic I-RRIP",
     ["baseline", "rrip", "irrip", "dyn_irrip"],
     ["ipc", "speedup", "l2c_load_mpki", "l2c_instr_load_mpki", "l2c_data_load_mpki", "dirrip_irrip_fraction"]),
    ("Task 2B: threshold sensitivity",
     ["SWEEP:dynT_"],
     ["ipc", "speedup", "l2c_load_mpki", "l2c_instr_load_mpki", "l2c_data_load_mpki", "dirrip_irrip_fraction"]),
    ("Task 3: SPP + prefetch-aware Dynamic I-RRIP",
     ["baseline", "spp", "dyn_irrip", "spp_dyn_irrip", "spp_pf_dyn_irrip"],
     ["ipc", "speedup", "l1d_pf_accuracy", "coverage", "l1d_load_mpki", "l2c_load_mpki",
      "l2c_instr_load_mpki", "l2c_data_load_mpki"]),
]

# geometric mean for ratios of performance, arithmetic mean for the rest
GEOMEAN_METRICS = {"ipc", "speedup"}


def expand_configs(spec, all_configs):
    out = []
    for c in spec:
        if c.startswith("SWEEP:"):
            prefix = c[len("SWEEP:"):]
            sweep = [x for x in all_configs if x.startswith(prefix)]
            sweep.sort(key=lambda x: fnum(x[len(prefix):]))
            out += sweep
        elif c in all_configs:
            out.append(c)
    return out


def fmt(v):
    if isinstance(v, float):
        if math.isnan(v):
            return "-"
        return "%.4f" % v if abs(v) < 10 else "%.2f" % v
    return str(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="results")
    ap.add_argument("--baseline", default="baseline", help="config used for speedup and coverage")
    args = ap.parse_args()

    if not os.path.isdir(args.results):
        sys.exit("no results directory %s" % args.results)

    data = {}   # config -> trace -> metrics
    for config in sorted(os.listdir(args.results)):
        cdir = os.path.join(args.results, config)
        if not os.path.isdir(cdir):
            continue
        for fn in sorted(os.listdir(cdir)):
            if not fn.endswith(".txt"):
                continue
            r = parse_file(os.path.join(cdir, fn))
            if r is None:
                print("skipping unfinished run %s/%s" % (config, fn), file=sys.stderr)
                continue
            data.setdefault(config, {})[fn[:-4]] = r

    if args.baseline not in data:
        print("warning: baseline config '%s' has no results; speedup/coverage will be empty" % args.baseline, file=sys.stderr)
    base = data.get(args.baseline, {})

    # derived metrics
    for config, traces in data.items():
        for trace, r in traces.items():
            b = base.get(trace)
            if b and b.get("ipc"):
                r["speedup"] = r.get("ipc", float("nan")) / b["ipc"]
            else:
                r["speedup"] = float("nan")
            if b and b.get("l1d_load_miss"):
                r["coverage"] = 100.0 * (b["l1d_load_miss"] - r.get("l1d_load_miss", 0)) / b["l1d_load_miss"]
            else:
                r["coverage"] = float("nan")

    # long CSV
    keys = sorted({k for traces in data.values() for r in traces.values() for k in r})
    csv_path = os.path.join(args.results, "summary.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["config", "trace"] + keys)
        for config in sorted(data):
            for trace in sorted(data[config]):
                r = data[config][trace]
                w.writerow([config, trace] + [r.get(k, "") for k in keys])
    print("wrote", csv_path)

    # markdown tables: one block per task, one table per metric (rows = traces, columns = configs)
    all_configs = sorted(data)
    md = ["# PA2 results", "",
          "Speedup and coverage are relative to `%s`. Coverage and accuracy are in %%. "
          "Mean row: geometric mean for IPC and speedup, arithmetic mean otherwise." % args.baseline, ""]
    for title, spec, metrics in TASKS:
        configs = expand_configs(spec, all_configs)
        if not configs:
            continue
        traces = sorted({t for c in configs for t in data[c]})
        md += ["## " + title, ""]
        for metric in metrics:
            md += ["### " + metric, "",
                   "| trace | " + " | ".join(configs) + " |",
                   "|---|" + "---|" * len(configs)]
            for t in traces:
                md.append("| %s | %s |" % (t, " | ".join(fmt(data[c].get(t, {}).get(metric, float("nan"))) for c in configs)))
            agg = geomean if metric in GEOMEAN_METRICS else amean
            md.append("| **mean** | %s |" % " | ".join(
                fmt(agg([data[c][t].get(metric, float("nan")) for t in data[c]])) for c in configs))
            md.append("")
    md_path = os.path.join(args.results, "tables.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))
    print("wrote", md_path)


if __name__ == "__main__":
    main()
