# PA2 implementation notes

> **After tuning** (results/tuning/tuning.md) the defaults changed: SPP ST 256 entries; I-RRIP data insertion at maxRRPV and eviction mode 2; dynamic I-RRIP with the ratio-only decision, threshold 0.40 and 16 leader sets per policy. Each changed value is marked `TUNED` in the code. The design notes below were written before tuning; where they disagree, the header comments in the code are current.

| Task | File | Doc |
|---|---|---|
| 1B SPP | `prefetcher/spp.l1d_pref` | [task1_spp.md](task1_spp.md) |
| 2A I-RRIP (+ `isInstruction` plumbing) | `replacement/irrip.l2c_repl`, `inc/cache.h`, `src/cache.cc`, `src/ooo_cpu.cc`, `replacement/*` | [task2a_irrip.md](task2a_irrip.md) |
| 2B Dynamic I-RRIP | `replacement/dyn_irrip.l2c_repl` (built with no prefetcher) | [task2b_dyn_irrip.md](task2b_dyn_irrip.md) |
| 3 Prefetch-aware Dynamic I-RRIP | `replacement/dyn_irrip.l2c_repl` (built with `spp`) | [task3_prefetch_aware.md](task3_prefetch_aware.md) |
| Report | `report/Summary.tex` | |

| Parameter tuning (all tasks) | `scripts/tune.py` | [tuning.md](tuning.md) |

## Where each deliverable is answered

The header comment of each code file answers the deliverables directly, with the numbers from your runs:

| Deliverable | Where |
|---|---|
| SPP storage overhead, bit by bit | `prefetcher/spp.l1d_pref` header, section B (also printed by every run) |
| How the accuracy thresholds were chosen | `spp.l1d_pref` section C |
| Why each accuracy range gets its degree | `spp.l1d_pref` section D |
| I-RRIP insertion, promotion and eviction rationale | `replacement/irrip.l2c_repl` header, section A |
| Number of leader sets and their placement | `replacement/dyn_irrip.l2c_repl` section 2B-2 |
| Decision logic | `dyn_irrip.l2c_repl` section 2B-3 |
| Threshold sensitivity (5 thresholds) and its explanation | `dyn_irrip.l2c_repl` section 2B-4 |
| Prefetch-packet handling in Task 3 and why | `dyn_irrip.l2c_repl` section 3-2 |
| Tunable parameters, which are worth tuning and why | each file's parameter section, and [tuning.md](tuning.md) |
| Results and why they match the design | each file's results section |

Each code file has every knob in one `TUNABLE PARAMETERS` block, and every knob is `#ifndef`-guarded. A variant is a two-line wrapper (`scripts/make_variant.sh`), so sweeps never edit the real files.

> This code was written and reviewed by reading it, without compiling or running it. Build every configuration once (`scripts/build_all.sh`) before starting the long runs. Compiler errors will show up there in a few minutes rather than hours into a sweep.

## Workflow

Run everything from the repository root. Traces are `traces/Trace1.gz`, `traces/Trace2.gz` and `traces/Trace3.gz` (`TRACE_DIR=...` overrides the directory).

```bash
scripts/build_all.sh                       # 7 configs: baseline ip_stride spp rrip irrip dyn_irrip spp_pf_dyn_irrip
JOBS=8 scripts/run_all.sh                  # 25M warmup + 25M sim on every trace -> results/<config>/<trace>.txt (resumable)
scripts/run_sweep.sh                       # Task 2B: 5 thresholds -> results/dynT_<t>/
python3 scripts/parse_results.py           # -> results/summary.csv, results/tables.md
python3 scripts/plot_results.py            # -> results/plots/*.png   (pip install matplotlib)
python3 scripts/tune.py list               # then `run`, `report`, `final`: see tuning.md
```

**Plot conventions** (the same in every chart, as in the problem statement's examples): the baseline (no prefetcher, LRU L2) is never a bar or a legend entry. It is a dotted reference line: 1.0 for speedup, 0 for coverage, and a dotted tick at the baseline's value over each trace for IPC and MPKI. Speedup bars grow up or down from 1.0. Each configuration keeps one colour in every chart. The last group is the geomean for IPC and speedup and the arithmetic mean otherwise.

The config list lives in `scripts/configs.sh`. Speedup and coverage use `baseline` (`no` prefetcher, `lru` L2) as the spec defines them.

## Review of your Task 1 design

**Verdict: the design is sufficient and matches the spec and hints**: a per-IP table, uniform-first with non-uniform fallback, a signature that maps to the next stride, degree > 1 with lookahead, accuracy-based throttling and a page-boundary check. The implementation follows it. These points had to change for it to work, or to stay lightweight:

1. **Train the signature and PT on every access, not only after the uniform break.** Step 3 of your state machine shifts a stride into the signature only on a mismatch, but your walkthrough updates it on every access. Only the walkthrough version works: otherwise the PT is cold at the moment of fallback, and the signature misses the strides seen during the uniform phase.
2. **3-bit strides are too narrow.** A 4 KB page reaches ±63 blocks, so the stride field is 7-bit signed. The signature is the SPP-style hash `(sig << 3) ^ stride` truncated to 10 bits, which keeps about 3 strides of history. That is the minimum the spec's (+1,+2,+2,+2,+3) example needs, because a 2-stride history cannot tell "+2,+2 → +2" from "+2,+2 → +3". Strides too large to prefetch are stored as a "far" code instead of being dropped, so the history stays intact.
3. **The FSM needs a way back to UNIFORM, with hysteresis.** Your FSM has no way back to UNIFORM, so an IP would stay non-uniform forever after one glitch. If the way back were instant, a short run of equal strides inside a non-uniform pattern would steal priority from a correct PT prediction. NON_UNIFORM → UNIFORM therefore needs 3 consecutive equal strides.
4. **Degree 0 cannot be a dead end.** At degree 0 nothing is issued, so accuracy is never measured again and the prefetcher stays off. In this implementation degree 0 is a shadow mode that records its first prediction in the filter without issuing it, so it can recover.
5. **Replace the 64-address circular buffer with a direct-mapped filter.** A buffer of 64 full addresses is a 64-way CAM (64 × 58 bits) searched on every access. A 256-entry direct-mapped filter with 8-bit partial tags, a useful bit and a class bit costs 2,816 bits, has 4× the reach, gives O(1) lookup, and also drops duplicate prefetches. `l1d_prefetcher_cache_fill()` is not needed.
6. **Halve the counters instead of resetting them.** Halving every epoch (an exponential moving average) keeps one bad epoch from swinging the degree. The degree is read straight from the accuracy-range table, which is what the "degrees for the different accuracy ranges" deliverable asks you to justify.
7. **Keep only the low 16 bits of the last address.** Storing the full block address per IP inflates the storage figure. Strides are computed modulo 2^16 instead, which is honest and loses nothing because only ±63 can be prefetched.
8. **Report ChampSim's accuracy in the tables.** The spec's accuracy is ChampSim's `useful / issued to lower level`; use that in the tables. The SPP's own filter accuracy (also printed, split by uniform and pattern prefetches) is what drives throttling and is useful for explaining results.

Choices where your text forked, and the default I picked: initial degree 2 (not 1); maximum degree 4 at ≥ 75% accuracy; a uniform pattern prefetches after 1 repeat, the same as `ip_stride`; the PT is shared by all IPs (indexed by signature only, as you proposed), so IPs with the same pattern train it together.

## Task 2 and 3 ideas

What I kept, changed and dropped from your Task 2B and Task 3 notes, with the reasons, is in the "What I took from your ... ideas" table in each task's doc. In short:

- **Task 2A**: kept your insertion and promotion as written, and added a data-first tie-break at eviction so all three parts are actually modified, as the spec asks.
- **Task 2B**: kept set dueling, the α weight, hysteresis and epochs. Replaced global-only counters and the miss-rate gate with "the spec's ratio on SRRIP leaders AND a duel guard".
- **Task 3**: kept distant prefetch insertion, and made it depend on the fill level and on measured L2 prefetch reuse. Dropped the region table, the L2→L1 piggyback bits and promotion on prefetch hits. ChampSim has no response-path hint mechanism, and none of those ideas act on the data lines SPP brings into L2.

## Other repo changes

- `src/main.cc`: single-core runs now print `l2c_replacement_final_stats()` (before this, it was only printed for multi-core).
- Not touched: `inc/champsim.h.bak`, `inc/champsim.h.cp` and `inc/sedD2leEO` are leftovers from `build_champsim.sh`'s `sed -i.bak` and can be deleted before submission.
