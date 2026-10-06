# Task 3: SPP with prefetch-aware Dynamic I-RRIP

Code: `PREFETCH_AWARE 1` paths in [`replacement/dyn_irrip.l2c_repl`](../replacement/dyn_irrip.l2c_repl). The spec asks for Task 3 to live in that file. [`replacement/pf_dyn_irrip.l2c_repl`](../replacement/pf_dyn_irrip.l2c_repl) is a two-line wrapper that switches the knob on, so both binaries can exist at once:

```
./build_champsim.sh spp pf_dyn_irrip spp_pf_dyn_irrip     # Task 3
./build_champsim.sh spp dyn_irrip    spp_dyn_irrip        # ablation: same pair without prefetch awareness
```

## The problem

With SPP at the L1D, the L2 receives `PREFETCH` packets from the L1D. Without awareness, Task 2B treats them as data demand:

1. A prefetch fill is inserted at RRPV 2, just like a demand data miss, so speculative lines compete with instruction lines and reused data.
2. A prefetch request that hits in L2 is promoted like a data hit. It is not a core re-reference: the L1D asked for a line L2 already had.
3. Most SPP prefetches are `FILL_L1`. The L1D gets a copy, so the core's later reads hit in the L1D and the **L2 copy is rarely read**. Keeping it at demand priority wastes L2 space.

"SPP alone" uses LRU at L2, which is worse still: every prefetch fill becomes MRU.

## Design

Prefetch awareness applies to every set, leaders included, so the duel is still a fair I-RRIP vs. SRRIP comparison.

| Event | Handling | Rationale |
|---|---|---|
| Prefetch fill, `FILL_L1` | RRPV 3 (first to go), or 2 when recent L2 prefetch reuse ≥ 50% | The L1D holds the useful copy, so the L2 copy is insurance. Distant insertion is PACMan-M (Wu et al. MICRO'11). The reuse test re-enables normal insertion for workloads where prefetched lines are reread from L2 (L1D thrashing). |
| Prefetch fill, `FILL_L2` | RRPV 2 (same as demand data) | The L1D MSHR was busy, so L2 holds the only copy and the core will read it from L2. |
| Prefetch request hits in L2 | No RRPV change | PACMan-H: not a reuse by the core. |
| First demand hit on a prefetched line | RRPV becomes the demand insertion value (only ever moved closer) | That access is the line's first real use. It should be treated like the demand miss it saved, not as evidence of reuse. Later hits promote normally. |
| Eviction | Among lines at RRPV 3: never-demanded prefetched line first, then data, then instructions | Uses the per-line prefetch bit `BLOCK` already has. Protects instruction lines from SPP pollution, which is the interaction that lets the pair beat each part alone. |
| Dueling counters | Prefetch packets never counted | Prefetch traffic would inflate "total accesses" and dilute the instruction ratio, so the ratio would no longer measure what the spec intends. |

**L2 prefetch reuse** = prefetched lines that received a demand hit / (those + prefetched lines evicted without one). It is computed by the policy itself: the demand hit is seen in `update_replacement_state` before `cache.cc` clears the prefetch bit, and the evicted victim is still in `block[set][way]` during the fill call. The two 16-bit counters are halved every epoch together with the dueling counters.

**Where the "hint bits" come from.** The packet already carries `type == PREFETCH` (your `is_prefetch` bit) and `fill_level` (FILL_L1 or FILL_L2). The policy reads `fill_level` from `MSHR.entry[MSHR.next_fill_index]`, which is the request being filled when `handle_fill` calls the policy. The address is checked against it, and if they do not match the policy falls back to the reuse rule. No bus or packet change was needed.

Extra storage: 1 prefetch bit per line (8,192), already present in `BLOCK` for ChampSim's prefetch statistics, plus 2×16-bit counters and 1 bit, for 8,225 bits in total.

## What I took from your Task 3 ideas

| Idea | Verdict |
|---|---|
| `is_prefetch` hint bit on the request | **Already exists** (`type == PREFETCH`). No change needed. |
| Prefetch fills inserted distant (2 or 3) | **Kept**, at 3 by default, with 2 for L2-only fills and high-reuse phases. |
| Demand hit on any line → RRPV 0 | **Modified**: the first demand hit on a prefetched line counts as its insertion, later hits promote as usual. |
| Prefetch hit → decrement RRPV | **Dropped**: a prefetch hit is not a reuse, and promoting on it is the pollution PACMan measured. It is available as `DIRRIP_PF_HIT_PROMOTE=1` for an ablation. |
| Region Prediction Table for instruction regions (1–2 KB) | **Dropped.** SPP is an L1D data prefetcher, so no prefetched line at L2 is an instruction line, and instruction-region locality says nothing about whether a data prefetch will be reused. It would add storage without touching the lines Task 3 is about. Dynamic I-RRIP already handles instruction priority. |
| Piggyback `rpt_status` / `prefetch_hit` bits L2 → L1 and SPP throttling from them | **Dropped.** It needs changes to `return_data`, `PACKET` and the L1D prefetcher interface. SPP already measures its own accuracy directly at the L1D, which is the quantity that throttling needs. An L2 "locality" bit for instruction regions is unrelated to data-prefetch accuracy. |
| Region-locality bit in the SPP signature table | **Dropped**, for the same reason. |

## Why it should beat both parts alone

- vs. **SPP alone** (SPP + LRU L2): same L1D coverage, plus I-RRIP's lower L2 instruction MPKI, plus L2 no longer giving MRU priority to every prefetched line.
- vs. **dynamic I-RRIP alone**: same L2 policy, plus SPP's L1D coverage. The prefetch handling keeps SPP's extra L2 traffic from evicting the instruction lines I-RRIP protects.
- The `spp_dyn_irrip` ablation isolates the effect of prefetch awareness. If Task 3 does not beat both on some trace, look at the `DIRRIP pf_fill` and `SPP` lines in that run. The usual fixes are a stricter SPP (`SPP_PT_LOOKAHEAD_CONF=3`, lower `SPP_DEGREE_HIGH`) or `DIRRIP_PF_REUSE_HIGH` 0.3–0.7.

## Tunable parameters (Task 3 only; Task 2B knobs still apply)

| Knob | Default | Range | Trade-off | Class |
|---|---|---|---|---|
| `PREFETCH_AWARE` | 0 (1 in the wrapper) | 0 / 1 | Task 2B vs. Task 3 | Fixed per build |
| `DIRRIP_PF_INSERT_FAR` | 3 | 2–3 | Insertion for FILL_L1 prefetches under low reuse | **Tune** |
| `DIRRIP_PF_INSERT_NEAR` | 2 | 1–2 | Insertion for L2-only or high-reuse prefetches | Tune |
| `DIRRIP_PF_USE_FILL_LEVEL` | 1 | 0 / 1 | Use the FILL_L2 hint | Tune (ablation) |
| `DIRRIP_PF_ADAPTIVE` | 1 | 0 / 1 | Reuse-driven insertion | Tune (ablation) |
| `DIRRIP_PF_REUSE_HIGH` | 0.50 | 0.2–0.8 | Reuse needed for near insertion | **Tune** |
| `DIRRIP_PF_HIT_PROMOTE` | 0 | 0 / 1 | Promote on prefetch hits (your original idea) | Ablation |
| `DIRRIP_PF_FIRST_USE_AS_INSERT` | 1 | 0 / 1 | First demand use as insertion | Ablation |
| `DIRRIP_PF_EVICT_FIRST` | 1 | 0 / 1 | Unused prefetched lines evicted first | Ablation |

Report metrics: IPC, speedup, L1D accuracy, coverage, and L1D / L2C / L2C instruction / L2C data load MPKI for `baseline`, `spp`, `dyn_irrip`, `spp_dyn_irrip` and `spp_pf_dyn_irrip` (the Task 3 table in `results/tables.md`).
