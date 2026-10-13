# Parameter tuning

`scripts/tune.py` builds and runs every tuning experiment, ranks the variants and plots them. Each experiment point is a two-line wrapper file that `#define`s a few knobs and `#include`s the real policy file, so the three source files are never edited.

```bash
python3 scripts/tune.py list                     # experiments, run counts, time estimate
python3 scripts/tune.py run task1 --jobs 8       # build + run every Task 1 experiment (resumable)
python3 scripts/tune.py run t2b_thr t2b_cost     # or single experiments
python3 scripts/tune.py run all --dry-run        # print the builds and runs only
python3 scripts/tune.py report all               # -> results/tuning/tuning.md + one PNG per experiment
python3 scripts/tune.py final task1 task2b       # merge each task's winners into one build and run it
```

`run` and `final` also take `--short` (10M warmup + 10M simulation into `results/tuning_short/`, with short runs of the baseline and the defaults to compare against) and `--rebuild`. Builds run one at a time because `build_champsim.sh` rewrites shared files; simulations run in parallel. Traces, warmup and simulation length come from `scripts/configs.sh` (`traces/Trace{1,2,3}.gz`, 25M + 25M), the same as `run_all.sh`. The generated wrapper files (`prefetcher/tp_*`, `replacement/tr_*`) are git-ignored.

## How the search stays small

A full factorial over the knobs would be about 10^9 points. Five rules bring it down to 55 builds and 165 runs, which is about 2 h 20 min of simulation at 4 parallel jobs (the script measures the run time from your existing outputs and prints the estimate).

1. **Layered, one factor at a time.** The L1D prefetcher only changes the L1D miss stream, and the L2 policy only changes what stays in the L2. So Task 1 knobs are tuned with LRU at the L2, Task 2 knobs with no prefetcher, and Task 3 tunes only its prefetch-handling knobs with both of the others at their defaults. Each experiment moves one knob, or one coupled group such as the whole degree table, around the default. `final` then runs the combination of the winners once, which catches any interaction that matters.
2. **Ordered by evidence.** The runs you already have point to the knobs most likely to matter, and those experiments come first in the list, so the tail can be skipped if time is short. For example, 54–70% of demand loads miss in SPP's 64-entry signature table, so table size is the first Task 1 experiment.
3. **Equivalence pruning.** Values that provably behave the same as a value already measured are not run.
   - The dueling ratio averages 0.27–0.60 across the traces. Any threshold well below that means "the ratio always wants I-RRIP", which is why `dynT_0.05` and `dynT_0.10` are identical on Trace1 and Trace2. The new sweep therefore covers 0.30–0.70.
   - L2 prefetch reuse is 2–4%, so a reuse threshold of 0.5 never fires. Only thresholds below 0.1 can change anything.
4. **One run per point.** ChampSim is deterministic, so there are no repetitions. A geomean speedup difference under 0.1% counts as a tie, and a tie keeps the default (or the cheaper option).
5. **Optional screening.** `--short` costs 40% of a full run. 10M warmup still fills the 512 KB L2 and the 2 MB LLC many times over, and only the ranking is used. Rerun the top two of each experiment at full length.

Each experiment's values are one step on each side of the default: the next power of two for sizes, about ±0.1 for thresholds, and the other settings for modes. These knobs respond monotonically or with a single peak, so one step each way shows which direction pays. A second step is only worth running in the direction that won.

## Experiments

| Experiment | Task | Points | What it answers |
|---|---|---|---|
| `t1_st` | 1 | ST 32 / 128 / 256 | Coverage lost to signature-table misses (the largest lever) |
| `t1_deg` | 1 | fixed 3, 6/4/3/1/0, 3/2/1/1/0 | Degree per accuracy range (deliverable); "fixed 3" shows what throttling buys |
| `t1_acc` | 1 | all thresholds ±0.1 | Accuracy thresholds (deliverable); whether the result sits on a band edge |
| `t1_pt` | 1 | PT confidence 1 / 3, lookahead confidence 3 | Accuracy vs. coverage of the 72% of prefetches that come from the PT |
| `t1_sig` | 1 | 8 / 12 bits, shift 2 | History length and PT size (8 bits saves 6.9 Kbit) |
| `t1_fsm` | 1 | uniform confidence 2, re-entry 2 | Uniform-stride FSM |
| `t1_epoch` | 1 | 256 / 4096 | Throttling reaction speed vs. noise |
| `t1_filter` | 1 | 128 / 512 | Filter reach (accuracy checker and duplicate filter) |
| `t2a_insert` | 2A | instructions at 0; data at 3 | Insertion rationale |
| `t2a_promo` | 2A | data hit → 0; data hit −2 | Promotion rationale |
| `t2a_evict` | 2A | plain SRRIP victim; mode 2 | Eviction rationale (what the tie-break buys) |
| `t2b_thr` | 2B | T = 0.30 … 0.70 (mode 2) | Threshold sensitivity (deliverable), re-centred |
| `t2b_thr_ratio` | 2B | T = 0.30 … 0.70 (ratio only) | The threshold without the duel guard (exactly the spec formula) |
| `t2b_mode` | 2B | ratio only; duel only | Decision logic (deliverable) |
| `t2b_leaders` | 2B | 16 / 64 per policy | Number of leader sets (deliverable) |
| `t2b_epoch` | 2B | 4K / 64K accesses | Decision interval |
| `t2b_cost` | 2B | c = 1 / 4 / 8 | Instruction-miss weight in the guard; break-even on Trace2 is c ≈ 7.6 |
| `t2b_hyst` | 2B | no band; wide band + 5% margin | Switching stability |
| `t3_insert` | 3 | far = 2; everything far | Distant prefetch insertion |
| `t3_ablate` | 3 | each mechanism off; hit promotion on | What each piece of prefetch awareness contributes |
| `t3_reuse` | 3 | 0.03 / 0.08 | Adaptive near insertion at thresholds that can actually fire |
| `t3_spp` | 3 | aggressive SPP degrees | Whether the prefetch-aware L2 lets SPP be more aggressive |

`spp_dyn_irrip` (already in `results/`) is the Task 3 ablation with all prefetch awareness off.

## What to expect

These are predictions from the existing results, to be checked against the runs.

- **`t1_st`** should raise coverage on Trace2, where 70% of loads miss in the signature table. Trace3 misses only 16% and should barely move.
- **`t2b_cost` (c = 8) and `t2b_mode` (ratio only)** should close most of the gap between dynamic I-RRIP (1.0062) and static I-RRIP (1.0081). On these traces I-RRIP is the better policy in almost every phase, and the guard's c = 2 under-weights instruction misses.
- **`t2b_thr`** should finally show the switch happening. Speedup should fall as T rises past each trace's ratio: 0.27 for Trace3, 0.43 for Trace1 and 0.60 for Trace2.
- **`t3_reuse` and `t3_insert`** should move results by less than the noise margin. Prefetch fills are a few percent of L2 fills, and 96% of the prefetched L2 lines are never read.
