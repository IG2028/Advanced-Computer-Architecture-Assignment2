#!/usr/bin/env python3
"""
Parameter-tuning experiments for PA2.

Every knob in spp.l1d_pref, irrip.l2c_repl and dyn_irrip.l2c_repl is
#ifndef-guarded, so an experiment point is a two-line wrapper file that
#defines a few knobs and #includes the real file. This script generates those
wrappers, builds them (sequentially, because build_champsim.sh rewrites shared
files), runs them on the traces in parallel, and ranks the results.

  python3 scripts/tune.py list                       # experiments, run counts, time estimate
  python3 scripts/tune.py run t1_st t1_deg           # build + run some experiments
  python3 scripts/tune.py run task1                  # every experiment of a task (task1, task2a, task2b, task3)
  python3 scripts/tune.py run all --jobs 8
  python3 scripts/tune.py run t1_st --short          # 10M+10M screening runs (separate results dir)
  python3 scripts/tune.py run t1_st --dry-run        # print what would happen
  python3 scripts/tune.py report all                 # rank variants -> results/tuning/*.md + *.png
  python3 scripts/tune.py final task1                # build + run the per-experiment winners combined

Traces, warmup and simulation length come from scripts/configs.sh
(TRACE_DIR, WARMUP, SIM, and Trace1/2/3.gz as in run_all.sh).

-----------------------------------------------------------------------------
How the search is kept small (why this does not take forever)
-----------------------------------------------------------------------------
The knobs below would be ~10^9 points as a full factorial. Five rules cut it
to ~60 builds and ~180 runs (a run is ~2 min on the provided traces, so about
1.5 h of simulation at 4 parallel jobs plus ~1 h of builds):

1. Layered, one-factor-at-a-time (coordinate descent) instead of factorial.
   The L1D prefetcher only changes the L1D miss stream; the L2 policy only
   changes what stays in L2. So Task 1 knobs are tuned with LRU at L2,
   Task 2 knobs with no prefetcher, and Task 3 tunes only the prefetch-handling
   knobs with both of those frozen at their defaults. Each experiment moves one
   knob (or one coupled group, e.g. the whole degree table) around the
   default. `final` then combines the winners and runs that once, which
   catches the interactions that matter.
2. Ordered by evidence from the runs you already have, so the knobs most likely
   to matter are tried first and the rest can be skipped if time is short
   (see each experiment's `why`). E.g. the SPP signature table misses on 54% of
   accesses (st_misses / accesses in results/spp), so table size goes first.
3. Equivalence pruning. Values that provably give the same behaviour as one
   already measured are not run. The dynamic I-RRIP ratio on these traces lies
   between 0.27 and 0.60 (DIRRIP mean_ratio), so every threshold below ~0.25
   means "always want I-RRIP" and every one above ~0.65 means "never";
   results/dynT_0.05 and dynT_0.10 are identical on two traces for exactly this
   reason. The new sweep therefore covers 0.30-0.70. L2 prefetch reuse is ~3%
   (first_use / evicted_unused), so a reuse threshold of 0.5 can never fire and
   only values below 0.1 are worth testing.
4. Deterministic simulator, so one run per point and no repetitions. A
   difference below NOISE (0.1% geomean speedup) is called a tie, and a tie is
   resolved in favour of the default or the cheaper (smaller-storage) option.
5. Optional screening. `--short` runs 10M warmup + 10M simulation (40% of the
   cost) into results/tuning_short/; rerun only the top two of each
   experiment at full length. 10M warmup still fills the 512 KB L2 and 2 MB LLC
   many times over, and only the ranking is used from the short runs.
"""
import argparse
import concurrent.futures
import math
import os
import re
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from parse_results import parse_file, geomean, amean  # noqa: E402

NOISE = 0.001          # geomean speedup differences below this are ties
DRY = False            # --dry-run: print only, write no wrapper files

# ---------------------------------------------------------------------------
# Experiment table
#   pref / repl : base prefetcher / replacement (file name without extension)
#   ref         : existing config in results/ that is the experiment's default
#   metric      : secondary metric printed next to speedup when ranking
#   variants    : name -> {"pref": {KNOB: value}, "repl": {KNOB: value}}
#   why         : what the experiment tests and why these values (report text)
# ---------------------------------------------------------------------------
def P(**k):
    return {"pref": k, "repl": {}}


def R(**k):
    return {"pref": {}, "repl": k}


EXPERIMENTS = [
    # ------------------------------ Task 1 (SPP, LRU L2) ------------------------------
    dict(name="t1_st", task="task1", pref="spp", repl="lru", ref="spp", metric="coverage",
         why="Signature table size. The ST misses on 54% of demand loads at 64 entries, so the "
             "prefetcher never trains for most IPs; this is the most likely win. 256 is the cap (8-bit LRU).",
         variants={"st32": P(SPP_ST_ENTRIES=32), "st128": P(SPP_ST_ENTRIES=128), "st256": P(SPP_ST_ENTRIES=256)}),
    dict(name="t1_deg", task="task1", pref="spp", repl="lru", ref="spp", metric="l1d_pf_accuracy",
         why="Degree per accuracy range (deliverable). 'fixed3' turns throttling off (degree 3 everywhere, like "
             "ip_stride) to show what throttling buys; 'aggr' and 'cons' move the whole table up and down.",
         variants={"fixed3": P(SPP_DEGREE_HIGH=3, SPP_DEGREE_MID=3, SPP_DEGREE_LOW=3, SPP_DEGREE_POOR=3, SPP_DEGREE_TOXIC=3),
                   "aggr": P(SPP_DEGREE_HIGH=6, SPP_DEGREE_MID=4, SPP_DEGREE_LOW=3, SPP_DEGREE_POOR=1, SPP_DEGREE_TOXIC=0),
                   "cons": P(SPP_DEGREE_HIGH=3, SPP_DEGREE_MID=2, SPP_DEGREE_LOW=1, SPP_DEGREE_POOR=1, SPP_DEGREE_TOXIC=0)}),
    dict(name="t1_acc", task="task1", pref="spp", repl="lru", ref="spp", metric="l1d_pf_accuracy",
         why="Accuracy thresholds (deliverable). Filter accuracy sits at 67-70%, i.e. inside the MID band; "
             "'strict' and 'loose' shift every boundary by about 0.1 so the measured accuracy lands in a different band.",
         variants={"strict": P(SPP_ACC_HIGH=0.85, SPP_ACC_MID=0.65, SPP_ACC_LOW=0.35, SPP_ACC_TOXIC=0.15),
                   "loose": P(SPP_ACC_HIGH=0.65, SPP_ACC_MID=0.40, SPP_ACC_LOW=0.20, SPP_ACC_TOXIC=0.05)}),
    dict(name="t1_pt", task="task1", pref="spp", repl="lru", ref="spp", metric="l1d_pf_accuracy",
         why="Pattern-table confidence. 72% of SPP prefetches come from the pattern table, so its thresholds "
             "set most of the accuracy/coverage trade-off.",
         variants={"conf1": P(SPP_PT_CONF_THRESHOLD=1, SPP_PT_LOOKAHEAD_CONF=1),
                   "conf3": P(SPP_PT_CONF_THRESHOLD=3, SPP_PT_LOOKAHEAD_CONF=3),
                   "look3": P(SPP_PT_LOOKAHEAD_CONF=3)}),
    dict(name="t1_sig", task="task1", pref="spp", repl="lru", ref="spp", metric="coverage",
         why="Signature length / PT size: 8 bits = 256-entry PT (saves 6.9 Kbit), 12 bits = 4096 entries, "
             "shift 2 keeps ~5 strides of history instead of ~3.",
         variants={"sig8": P(SPP_SIG_BITS=8), "sig12": P(SPP_SIG_BITS=12), "shift2": P(SPP_SIG_SHIFT=2)}),
    dict(name="t1_fsm", task="task1", pref="spp", repl="lru", ref="spp", metric="l1d_pf_accuracy",
         why="Uniform-stride FSM: confidence needed before uniform prefetching, and the hysteresis to return "
             "from NON_UNIFORM to UNIFORM.",
         variants={"uconf2": P(SPP_UCONF_THRESHOLD=2), "reentry2": P(SPP_UNIFORM_REENTRY=2)}),
    dict(name="t1_epoch", task="task1", pref="spp", repl="lru", ref="spp", metric="l1d_pf_accuracy",
         why="Throttling epoch length: faster reaction vs. noisier accuracy estimates.",
         variants={"ep256": P(SPP_EPOCH_ACCESSES=256), "ep4096": P(SPP_EPOCH_ACCESSES=4096)}),
    dict(name="t1_filter", task="task1", pref="spp", repl="lru", ref="spp", metric="l1d_pf_accuracy",
         why="Prefetch-filter reach (accuracy checker + duplicate filter): 128 vs 512 entries.",
         variants={"f128": P(SPP_FILTER_INDEX_BITS=7), "f512": P(SPP_FILTER_INDEX_BITS=9)}),

    # ------------------------------ Task 2A (I-RRIP, no prefetcher) ------------------------------
    dict(name="t2a_insert", task="task2a", pref="no", repl="irrip", ref="irrip", metric="l2c_load_mpki",
         why="Insertion: instructions at 0 (stronger priority) and data at maxRRPV (data as eviction candidates).",
         variants={"instr0": R(IRRIP_INSERT_INSTR=0), "data3": R(IRRIP_INSERT_DATA=3)}),
    dict(name="t2a_promo", task="task2a", pref="no", repl="irrip", ref="irrip", metric="l2c_load_mpki",
         why="Promotion: SRRIP-style data hit -> 0 vs. the default one-step promotion, and a two-step promotion.",
         variants={"hp": R(IRRIP_DATA_HIT_MODE=0), "step2": R(IRRIP_DATA_HIT_STEP=2)}),
    dict(name="t2a_evict", task="task2a", pref="no", repl="irrip", ref="irrip", metric="l2c_load_mpki",
         why="Eviction: plain SRRIP victim choice (shows what the tie-break buys) vs. the stronger mode 2.",
         variants={"plain": R(IRRIP_EVICT_MODE=0), "strong": R(IRRIP_EVICT_MODE=2)}),

    # ------------------------------ Task 2B (dynamic I-RRIP, no prefetcher) ------------------------------
    dict(name="t2b_thr", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="5-point threshold sensitivity (deliverable), re-centred on the measured ratio range 0.27-0.60. "
             "Default decision mode (ratio AND duel guard).",
         variants={"t030": R(DIRRIP_THRESHOLD=0.30), "t040": R(DIRRIP_THRESHOLD=0.40), "t050": R(DIRRIP_THRESHOLD=0.50),
                   "t060": R(DIRRIP_THRESHOLD=0.60), "t070": R(DIRRIP_THRESHOLD=0.70)}),
    dict(name="t2b_thr_ratio", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="Same 5 thresholds with the ratio alone (exactly the spec's formula), isolating the threshold's effect "
             "from the duel guard.",
         variants={"t030": R(DIRRIP_DECISION_MODE=0, DIRRIP_THRESHOLD=0.30), "t040": R(DIRRIP_DECISION_MODE=0, DIRRIP_THRESHOLD=0.40),
                   "t050": R(DIRRIP_DECISION_MODE=0, DIRRIP_THRESHOLD=0.50), "t060": R(DIRRIP_DECISION_MODE=0, DIRRIP_THRESHOLD=0.60),
                   "t070": R(DIRRIP_DECISION_MODE=0, DIRRIP_THRESHOLD=0.70)}),
    dict(name="t2b_mode", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="Decision logic: ratio only vs. classic duel only vs. both (default).",
         variants={"ratio": R(DIRRIP_DECISION_MODE=0), "duel": R(DIRRIP_DECISION_MODE=1)}),
    dict(name="t2b_leaders", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="Leader sets per policy (deliverable): 16 = less sampling, fewer sets stuck on the wrong policy; "
             "64 = better estimates, 12.5% of the cache fixed.",
         variants={"l16": R(DIRRIP_LEADER_SETS=16), "l64": R(DIRRIP_LEADER_SETS=64)}),
    dict(name="t2b_epoch", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="Decision epoch: 4K accesses (fast, noisy; switches are already frequent at 16K) vs 64K (stable).",
         variants={"e4k": R(DIRRIP_EPOCH_ACCESSES=4096), "e64k": R(DIRRIP_EPOCH_ACCESSES=65536)}),
    dict(name="t2b_cost", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="Instruction-miss cost c in the duel guard. Static I-RRIP beats dynamic, so the guard falls back to "
             "SRRIP too often (69% of epochs on Trace2). Over the whole run on Trace2 the I-RRIP leaders saved 350 "
             "instruction misses and added 2,655 data misses against the SRRIP leaders, so the guard only prefers "
             "I-RRIP when c > 7.6; c4 and c8 bracket that break-even point, c1 shows the opposite end.",
         variants={"c1": R(DIRRIP_INSTR_MISS_COST=1.0), "c4": R(DIRRIP_INSTR_MISS_COST=4.0),
                   "c8": R(DIRRIP_INSTR_MISS_COST=8.0)}),
    dict(name="t2b_hyst", task="task2b", pref="no", repl="dyn_irrip", ref="dyn_irrip", metric="l2c_load_mpki",
         why="Hysteresis and guard margin: no band vs. a wide band plus a 5% guard margin (fewer switches).",
         variants={"h0": R(DIRRIP_HYSTERESIS=0.0), "h15": R(DIRRIP_HYSTERESIS=0.15, DIRRIP_GUARD_MARGIN=0.05)}),

    # ------------------------------ Task 3 (SPP + prefetch-aware dynamic I-RRIP) ------------------------------
    dict(name="t3_insert", task="task3", pref="spp", repl="dyn_irrip", ref="spp_pf_dyn_irrip", metric="l2c_load_mpki",
         why="Prefetch insertion position: far = maxRRPV-1 (same as demand data, i.e. no distant insertion), and "
             "everything far (L2-only prefetches too).",
         variants={"far2": R(DIRRIP_PF_INSERT_FAR=2), "allfar": R(DIRRIP_PF_USE_FILL_LEVEL=0, DIRRIP_PF_ADAPTIVE=0)}),
    dict(name="t3_ablate", task="task3", pref="spp", repl="dyn_irrip", ref="spp_pf_dyn_irrip", metric="l2c_load_mpki",
         why="Ablation of each prefetch-awareness mechanism (rationale deliverable): turning one off at a time "
             "shows what each contributes. spp_dyn_irrip already measures turning all of them off.",
         variants={"nofirstuse": R(DIRRIP_PF_FIRST_USE_AS_INSERT=0), "noevict": R(DIRRIP_PF_EVICT_FIRST=0),
                   "hitpromote": R(DIRRIP_PF_HIT_PROMOTE=1)}),
    dict(name="t3_reuse", task="task3", pref="spp", repl="dyn_irrip", ref="spp_pf_dyn_irrip", metric="l2c_load_mpki",
         why="Reuse threshold for near insertion. Measured L2 prefetch reuse is ~3%, so 0.5 never fires; only "
             "thresholds below 0.1 can change anything.",
         variants={"r003": R(DIRRIP_PF_REUSE_HIGH=0.03), "r008": R(DIRRIP_PF_REUSE_HIGH=0.08)}),
    dict(name="t3_spp", task="task3", pref="spp", repl="dyn_irrip", ref="spp_pf_dyn_irrip", metric="coverage",
         why="Cross-task interaction: with L2 now absorbing useless prefetches at maxRRPV, a more aggressive SPP "
             "may pay off where it did not with LRU.",
         variants={"aggr": {"pref": dict(SPP_DEGREE_HIGH=6, SPP_DEGREE_MID=4, SPP_DEGREE_LOW=3, SPP_DEGREE_POOR=1,
                                         SPP_DEGREE_TOXIC=0), "repl": {}}}),
]
EXP_BY_NAME = {e["name"]: e for e in EXPERIMENTS}
TASKS = sorted({e["task"] for e in EXPERIMENTS})


# ---------------------------------------------------------------------------
# configs.sh values (TRACE_DIR, WARMUP, SIM, RESULTS_DIR, JOBS)
# ---------------------------------------------------------------------------
def shell_config():
    out = subprocess.run(["bash", "-c", "source scripts/configs.sh; echo \"$TRACE_DIR|$WARMUP|$SIM|$RESULTS_DIR|$JOBS\""],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    trace_dir, warmup, sim, results, jobs = out.split("|")
    return trace_dir, int(warmup), int(sim), results, int(jobs)


def base_configs():
    """config name -> (l1d prefetcher, l2c replacement) from CONFIGS in scripts/configs.sh"""
    out = subprocess.run(["bash", "-c", "source scripts/configs.sh; printf '%s\\n' \"${CONFIGS[@]}\""],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return {c.split(":")[0]: tuple(c.split(":")[1:3]) for c in out}


def trace_files(trace_dir):
    # same trace set as scripts/run_all.sh
    names = ["Trace1.gz", "Trace2.gz", "Trace3.gz"]
    paths = [os.path.join(trace_dir, n) for n in names]
    return [p for p in paths if os.path.exists(os.path.join(ROOT, p))] or paths


def binary_for(pref, repl, name):
    return "bin/hashed_perceptron-no-%s-no-no-no-no-no-lru-lru-lru-%s-lru-lru-lru-lru-1core-%s" % (pref, repl, name)


def expand(names):
    out = []
    for n in names:
        if n == "all":
            out += [e["name"] for e in EXPERIMENTS]
        elif n in TASKS:
            out += [e["name"] for e in EXPERIMENTS if e["task"] == n]
        elif n in EXP_BY_NAME:
            out.append(n)
        else:
            sys.exit("unknown experiment or task: %s (see `tune.py list`)" % n)
    seen = set()
    return [n for n in out if not (n in seen or seen.add(n))]


# ---------------------------------------------------------------------------
# wrappers
# ---------------------------------------------------------------------------
def write_wrapper(kind, base, wrapper, knobs):
    """kind 'pref' -> prefetcher/<wrapper>.l1d_pref, 'repl' -> replacement/<wrapper>.l2c_repl"""
    d, ext = ("prefetcher", "l1d_pref") if kind == "pref" else ("replacement", "l2c_repl")
    path = os.path.join(ROOT, d, "%s.%s" % (wrapper, ext))
    lines = ["// Auto-generated by scripts/tune.py from %s.%s. Do not edit; re-generate instead." % (base, ext)]
    lines += ["#define %s %s" % (k, v) for k, v in knobs.items()]
    lines.append('#include "%s.%s"' % (base, ext))
    text = "\n".join(lines) + "\n"
    if DRY:
        return wrapper
    if not os.path.exists(path) or open(path).read() != text:
        with open(path, "w") as f:
            f.write(text)
    return wrapper


def config_points(exp, variant_names=None, override=None):
    """yield (config name, pref module, repl module, knobs) for an experiment"""
    variants = override if override is not None else exp["variants"]
    for v, spec in variants.items():
        if variant_names and v not in variant_names:
            continue
        cfg = "%s__%s" % (exp["name"], v)
        pref, repl = exp["pref"], exp["repl"]
        if spec["pref"]:
            pref = write_wrapper("pref", exp["pref"], "tp_%s" % cfg, spec["pref"])
        if spec["repl"]:
            repl = write_wrapper("repl", exp["repl"], "tr_%s" % cfg, spec["repl"])
        yield cfg, pref, repl, spec, exp.get("ref")


# ---------------------------------------------------------------------------
# time estimate from existing runs ("Simulation time: H hr M min S sec")
# ---------------------------------------------------------------------------
def seconds_per_run(results):
    secs = []
    for cfg in ("baseline", "spp", "dyn_irrip", "spp_pf_dyn_irrip"):
        d = os.path.join(ROOT, results, cfg)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            with open(os.path.join(d, fn), errors="replace") as f:
                for line in f:
                    m = re.search(r"Finished CPU 0 .*Simulation time: (\d+) hr (\d+) min (\d+) sec", line)
                    if m:
                        secs.append(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
    return sum(secs) / len(secs) if secs else 150.0


def fmt_time(s):
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_list(args):
    trace_dir, warmup, sim, results, jobs = shell_config()
    per_run = seconds_per_run(results)
    ntr = len(trace_files(trace_dir))
    total_v = 0
    print("%-14s %-7s %3s  %s" % ("experiment", "task", "pts", "why"))
    for e in EXPERIMENTS:
        total_v += len(e["variants"])
        print("%-14s %-7s %3d  %s" % (e["name"], e["task"], len(e["variants"]), e["why"]))
    runs = total_v * ntr
    est = runs * per_run / max(1, args.jobs or jobs) + total_v * 60
    print("\n%d variants x %d traces = %d runs; ~%.0f s per run measured -> ~%s at %d jobs (+~1 min per build)"
          % (total_v, ntr, runs, per_run, fmt_time(est), args.jobs or jobs))


def run_points(points, args):
    trace_dir, warmup, sim, results, jobs = shell_config()
    jobs = args.jobs or jobs
    if args.short:
        warmup, sim = 10000000, 10000000
    outroot = os.path.join("results", "tuning_short" if args.short else "tuning", "runs")
    traces = trace_files(trace_dir)

    # Short runs are only comparable with short runs, so --short also runs the
    # baseline and each experiment's default config (existing binaries from
    # build_all.sh) at the same length, into runs/ref__<config>/.
    if args.short:
        known = base_configs()
        refs = ["baseline"] + sorted({r for _, _, _, _, r in points if r})
        points = [("ref__" + r, known[r][0], known[r][1], None, None, r) for r in refs] + \
                 [p + (None,) for p in points]
    else:
        points = [p + (None,) for p in points]

    # 1) builds, one at a time (build_champsim.sh edits shared source files)
    for cfg, pref, repl, _, _, existing in points:
        if existing:
            if not os.path.exists(os.path.join(ROOT, binary_for(pref, repl, existing))):
                msg = "%s is not built; run scripts/build_all.sh first" % existing
                if not args.dry_run:
                    sys.exit(msg)
                print("note:", msg)
            continue
        binary = binary_for(pref, repl, cfg)
        if os.path.exists(os.path.join(ROOT, binary)) and not args.rebuild:
            continue
        cmd = ["./build_champsim.sh", pref, repl, cfg]
        print("build:", " ".join(cmd))
        if args.dry_run:
            continue
        log = os.path.join(ROOT, "build_%s.log" % cfg)
        with open(log, "w") as f:
            r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
        if r.returncode != 0 or not os.path.exists(os.path.join(ROOT, binary)):
            sys.exit("build of %s failed, see %s" % (cfg, log))
        os.remove(log)

    # 2) runs, in parallel; finished outputs are skipped (resumable)
    todo = []
    for cfg, pref, repl, _, _, existing in points:
        binary = binary_for(pref, repl, existing or cfg)
        if not args.dry_run:
            os.makedirs(os.path.join(ROOT, outroot, cfg), exist_ok=True)
        for t in traces:
            out = os.path.join(outroot, cfg, os.path.basename(t).split(".")[0] + ".txt")
            full = os.path.join(ROOT, out)
            if os.path.exists(full) and "ChampSim completed all CPUs" in open(full, errors="replace").read():
                continue
            todo.append("%s -warmup_instructions %d -simulation_instructions %d -traces %s > %s 2>&1"
                        % (binary, warmup, sim, shlex.quote(t), shlex.quote(out)))
    per_run = seconds_per_run(results) * (warmup + sim) / 50e6
    print("%d simulations to run with %d jobs (~%s)" % (len(todo), jobs, fmt_time(len(todo) * per_run / max(1, jobs))))
    if args.dry_run:
        for c in todo:
            print("run:", c)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(subprocess.run, c, shell=True, cwd=ROOT): c for c in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            rc = fut.result().returncode
            print("[%d/%d] %s %s" % (i, len(todo), "ok" if rc == 0 else "FAILED", futs[fut].split(" > ")[-1]))


def cmd_run(args):
    points = []
    for n in expand(args.experiments):
        points += list(config_points(EXP_BY_NAME[n], args.variants))
    run_points(points, args)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def load_config(path):
    rows = {}
    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if fn.endswith(".txt"):
                r = parse_file(os.path.join(path, fn))
                if r:
                    rows[fn[:-4]] = r
    return rows


def summarize(rows, base):
    """geomean speedup vs baseline + mean of every metric, over traces that have a baseline"""
    sp, cov = [], []
    for t, r in rows.items():
        b = base.get(t)
        if b and b.get("ipc"):
            sp.append(r["ipc"] / b["ipc"])
            if b.get("l1d_load_miss"):
                cov.append(100.0 * (b["l1d_load_miss"] - r.get("l1d_load_miss", 0)) / b["l1d_load_miss"])
    s = {"speedup": geomean(sp), "coverage": amean(cov), "traces": len(sp)}
    for k in ("l1d_load_mpki", "l1d_pf_accuracy", "l2c_load_mpki", "l2c_instr_load_mpki", "l2c_data_load_mpki",
              "dirrip_irrip_fraction"):
        s[k] = amean([r.get(k, float("nan")) for r in rows.values()])
    return s


def ranked(exp, short=False):
    sub = "tuning_short" if short else "tuning"
    if short:   # short variants are compared with short runs of the baseline and the default
        base = load_config(os.path.join(ROOT, "results", sub, "runs", "ref__baseline"))
        ref_rows = load_config(os.path.join(ROOT, "results", sub, "runs", "ref__" + exp["ref"]))
    else:
        base = load_config(os.path.join(ROOT, "results", "baseline"))
        # the defaults the experiments were run against were archived in
        # results/pre_tuning/ once the tuned values became the new defaults
        old = os.path.join(ROOT, "results", "pre_tuning", exp["ref"])
        ref_rows = load_config(old if os.path.isdir(old) else os.path.join(ROOT, "results", exp["ref"]))
    out = [("default (%s)" % exp["ref"], None, summarize(ref_rows, base))] if ref_rows else []
    for v, spec in exp["variants"].items():
        rows = load_config(os.path.join(ROOT, "results", sub, "runs", "%s__%s" % (exp["name"], v)))
        if rows:
            out.append((v, spec, summarize(rows, base)))
    out.sort(key=lambda x: -x[2]["speedup"] if not math.isnan(x[2]["speedup"]) else 0)
    return out


def knobs_str(spec):
    if not spec:
        return "(defaults)"
    return ", ".join("%s=%s" % kv for kv in list(spec["pref"].items()) + list(spec["repl"].items()))


def winner(exp, rows):
    """best variant if it beats the default by more than NOISE, else None (ties keep the default)"""
    ref = next((r for r in rows if r[1] is None), None)
    best = next((r for r in rows if r[1] is not None), None)
    if not best:
        return None
    ref_sp = ref[2]["speedup"] if ref else 1.0
    return best if best[2]["speedup"] - ref_sp > NOISE else None


def plot_experiment(exp, rows, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    names = [r[0] for r in rows]
    vals = [r[2]["speedup"] for r in rows]
    fig, ax = plt.subplots(figsize=(max(4.5, 0.9 * len(rows) + 1.5), 3.2))
    colors = ["#52514e" if r[1] is None else "#2a78d6" for r in rows]
    ax.bar(range(len(rows)), [v - 1.0 for v in vals], bottom=1.0, color=colors, width=0.6, edgecolor="white")
    ref = next((r for r in rows if r[1] is None), None)
    ax.axhline(1.0, color="#0b0b0b", linestyle=":", linewidth=1.0)
    note = "···· baseline (no prefetch, LRU) = 1.0"
    if ref:
        ax.axhline(ref[2]["speedup"], color="#52514e", linestyle=":", linewidth=1.0)
        note += "   ···· default"
    lo, hi = min(vals + [1.0]), max(vals + [1.0])
    pad = max(0.002, 0.15 * (hi - lo))
    ax.set_ylim(lo - pad, hi + pad)
    for i, v in enumerate(vals):
        ax.annotate("%.4f" % v, (i, v), textcoords="offset points", xytext=(0, 3), ha="center", fontsize=7, color="#52514e")
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(names, fontsize=8)
    ax.set_ylabel("geomean speedup", fontsize=9)
    ax.set_title(exp["name"], fontsize=10, loc="left")
    ax.text(1.0, 1.01, note, transform=ax.transAxes, ha="right", va="bottom", fontsize=7, color="#52514e")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.yaxis.grid(True, color="#e4e3df", linewidth=0.8)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return True


def cmd_report(args):
    outdir = os.path.join(ROOT, "results", "tuning_short" if args.short else "tuning")
    os.makedirs(outdir, exist_ok=True)
    md = ["# Tuning results", "",
          "Geomean speedup over `baseline`; other columns are means over traces. Differences under %.1f%% are ties "
          "and keep the default." % (NOISE * 100), ""]
    names = expand(args.experiments)
    exps = [EXP_BY_NAME[n] for n in names]
    for task in TASKS:   # a `final` run of a task, if one exists, is reported after its experiments
        e = combine(task, args.short)
        if e and any(x["task"] == task for x in exps):
            exps.append(e)
    for exp in exps:
        n = exp["name"]
        rows = ranked(exp, args.short)
        if len(rows) <= 1:
            continue
        w = winner(exp, rows)
        md += ["## %s (%s)" % (n, exp["task"]), "", exp["why"], "",
               "| variant | knobs | speedup | %s | L2C instr MPKI | L2C data MPKI |" % exp["metric"],
               "|---|---|---|---|---|---|"]
        for name, spec, s in rows:
            md.append("| %s | %s | %.4f | %.3f | %.3f | %.3f |" % (
                ("**%s**" % name) if w and w[0] == name else name, knobs_str(spec), s["speedup"],
                s.get(exp["metric"], float("nan")), s["l2c_instr_load_mpki"], s["l2c_data_load_mpki"]))
        md += ["", "Winner: %s" % (("`%s` (%s)" % (w[0], knobs_str(w[1]))) if w else "keep the default (no variant beats it by more than the noise margin)"), ""]
        png = os.path.join(outdir, "%s.png" % n)
        if plot_experiment(exp, rows, png):
            md += ["![%s](%s.png)" % (n, n), ""]
    path = os.path.join(outdir, "tuning.md")
    with open(path, "w") as f:
        f.write("\n".join(md))
    print("wrote", path)


def combine(task, short, verbose=False):
    """the per-experiment winners of one task merged into one variant -> pseudo-experiment, or None"""
    pref_k, repl_k, base, wins = {}, {}, None, []
    for e in EXPERIMENTS:
        if e["task"] != task:
            continue
        base = base or e
        w = winner(e, ranked(e, short))
        if w:
            wins.append((w[2]["speedup"], w))
        if verbose:
            print("%s: %s" % (e["name"], ("take %s (%s, speedup %.4f)" % (w[0], knobs_str(w[1]), w[2]["speedup"]))
                                          if w else "keep default"))
    if not wins:
        return None
    # Two winners can set the same knob (t2b_thr, t2b_thr_ratio and t2b_mode all
    # touch the decision): apply them weakest first so the best one's value wins.
    for _, w in sorted(wins, key=lambda x: x[0]):
        pref_k.update(w[1]["pref"])
        repl_k.update(w[1]["repl"])
    return dict(base, name="%s_final" % task, why="The per-experiment winners of %s combined into one build." % task,
                variants={"best": {"pref": pref_k, "repl": repl_k}})


def cmd_final(args):
    """combine the per-experiment winners of one task into one variant and run it"""
    pts, finals = [], []
    for task in args.tasks:
        if task not in TASKS:
            sys.exit("final takes a task name: %s" % ", ".join(TASKS))
        exp = combine(task, args.short, verbose=True)
        if not exp:
            print("%s: no winners, the defaults stay" % task)
            continue
        finals.append(exp)
        pts += list(config_points(exp))
    if pts:
        run_points(pts, args)
        if not args.dry_run:
            for e in finals:
                for name, spec, s in ranked(e, args.short):
                    print("%-28s speedup %.4f  %s" % (e["name"] + ":" + name, s["speedup"], knobs_str(spec)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--jobs", type=int, default=0)
    for name in ("run", "report"):
        p = sub.add_parser(name)
        p.add_argument("experiments", nargs="+", help="experiment names, task names (task1 ...) or all")
        p.add_argument("--short", action="store_true", help="10M warmup + 10M sim screening runs")
        if name == "run":
            p.add_argument("--jobs", type=int, default=0)
            p.add_argument("--variants", nargs="*", help="only these variant names")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--rebuild", action="store_true")
    p = sub.add_parser("final")
    p.add_argument("tasks", nargs="+")
    p.add_argument("--short", action="store_true")
    p.add_argument("--jobs", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    os.chdir(ROOT)
    global DRY
    DRY = getattr(args, "dry_run", False)
    {"list": cmd_list, "run": cmd_run, "report": cmd_report, "final": cmd_final}[args.cmd](args)


if __name__ == "__main__":
    main()
