# Task 1B: Stride Pattern Prefetcher (SPP)

Code: [`prefetcher/spp.l1d_pref`](../prefetcher/spp.l1d_pref). Build with `./build_champsim.sh spp lru spp`.

## How it works

Three tables, all sized by knobs in the single `TUNABLE PARAMETERS` block at the top of the file.

| Structure | Indexed by | Entry fields (bits at defaults) |
|---|---|---|
| Signature Table (ST), 64 entries, fully associative, LRU | hashed IP | valid 1, IP tag 10, last block address (low 16 bits) 16, last stride 7, signature 10, FSM state 2, uniform confidence 2, LRU 6 = **54** |
| Pattern Table (PT), 1024 entries, direct mapped | signature | next stride 7, confidence 2 = **9** |
| Prefetch filter, 256 entries, direct mapped | low 8 bits of the block address | valid 1, tag 8, useful 1, class 1 = **11** |
| Throttle registers | | issued counter 16, useful counter 16, epoch counter 11, degree 3 = **46** |

**Total at the tuned defaults: 256×56 + 1024×9 + 256×11 + 46 = 14,336 + 9,216 + 2,816 + 46 = 26,414 bits (3,302 bytes).** (Before tuning, with a 64-entry ST: 15,534 bits.) `l1d_prefetcher_final_stats()` prints this breakdown, recomputed from the knobs, at the end of every run, so the number in the report always matches the build.

On every L1D demand load (`l1d_prefetcher_operate`):

1. **Accuracy check.** If the block hits an unused filter entry, that earlier prefetch was useful: set its useful bit and bump the useful counter. Late prefetches (demand arrives while the prefetch is in flight) also count, because they were correct predictions.
2. **Throttle.** Every 1,024 demand loads, accuracy = useful / issued (both counters halved afterwards, so older epochs fade out) is mapped to a degree.
3. **ST lookup.** A miss allocates the LRU entry and returns; the next access gives the first stride.
4. **Stride.** Computed from the truncated last address (sign-extended modulo 2^16). A stride of 0 is ignored. A stride outside ±63 blocks becomes the "far" code: it cannot be prefetched inside a 4 KB page, but it stays in the history, so patterns such as "jump to the next row, then +1, +1" are still learned.
5. **Train (always, in every state).** FSM update, then `PT[old signature]` learns the stride that followed it, then `signature = ((signature << 3) ^ stride7) & 0x3FF`.
6. **Predict.** In UNIFORM with uniform confidence ≥ 1, prefetch `addr + k·stride` for k = 1..degree. Otherwise walk the PT: each step needs PT confidence ≥ 2, uses the predicted stride, and advances a speculative signature. That is the lookahead in your walkthrough. The walk stops at the degree, a low-confidence entry, or the page boundary. A block already in the filter is skipped, not re-issued. Fill level follows `ip_stride`: L1 while the L1D MSHR is under half full, otherwise L2.

### Per-IP FSM (2 bits)

```
INIT --(first stride)--> UNIFORM --(stride != last stride)--> NON_UNIFORM
                            ^                                      |
                            +---- uniform confidence reaches 3 ----+
```

UNIFORM has priority whenever its confidence is high. A break drops the IP to NON_UNIFORM, where the PT predicts. The IP returns to UNIFORM only after 3 consecutive equal strides. That hysteresis matters for the spec example (+1,+2,+2,+2,+3)^n: its run of two equal +2 strides must not hand control back to a uniform prediction of +2 when the PT knows +3 comes next.

### Throttling table

| Accuracy (filter) | Degree | Why |
|---|---|---|
| ≥ 0.75 | 4 | Nearly every prefetch is used, so go further ahead for timeliness. 4 keeps a trigger's prefetches within the 16-entry L1D PQ alongside other IPs. |
| 0.50 – 0.75 | 3 | Same as the provided IP-stride degree. |
| 0.25 – 0.50 | 2 | Half or more of the prefetches are wasted, so only the near, most reliable steps are issued. |
| 0.10 – 0.25 | 1 | Mostly useless, but the first step is the most accurate one. |
| < 0.10 | 0 (shadow) | Nothing is issued. The first prediction is still written to the filter, so accuracy keeps being measured and the prefetcher can come back on. With a plain degree 0 it could never recover. |

Thresholds 0.75 / 0.5 / 0.25 split the range evenly. The 0.10 "toxic" cut comes from prefetcher throttling work (FDP, Srinath et al. HPCA'07, uses around 0.40 / 0.75 for its accuracy levels). These are the values to sweep for the "how did you decide the thresholds" deliverable, for example `scripts/make_variant.sh prefetcher/spp.l1d_pref spp_acc_hi SPP_ACC_HIGH=0.85 SPP_DEGREE_HIGH=6`.

## Tunable parameters

"Tune" means a real design choice worth sweeping. "Fixed" means it follows from the system or the spec, and changing it only costs storage or breaks an invariant. "Leave" means it has a second-order effect.

| Knob | Default | Range | Trade-off | Class |
|---|---|---|---|---|
| `SPP_ST_ENTRIES` | 256 (tuned; was 64) | 16–256 | More IPs tracked concurrently vs. 54 bits each and a wider fully associative search | Tune |
| `SPP_IP_TAG_BITS` | 10 | 6–16 | Fewer bits cause IP aliasing (`ip_stride` uses 8) | Leave |
| `SPP_ADDR_BITS` | 16 | 12–58 | Stride is computed modulo 2^bits. 16 is ample, since only ±63 is usable | Fixed |
| `SPP_STRIDE_BITS` | 7 | 7 | ±63 blocks covers every in-page target of a 64-block page | Fixed |
| `SPP_UCONF_BITS` | 2 | 1–3 | Number of FSM confidence states | Leave |
| `SPP_UCONF_THRESHOLD` | 1 | 1–3 | Repeats before uniform prefetching. 1 matches `ip_stride`; higher is more accurate but later | Tune |
| `SPP_UNIFORM_REENTRY` | 3 | 1–max | Hysteresis for NON_UNIFORM → UNIFORM. Low values let short equal-stride runs override the PT | Tune |
| `SPP_SIG_BITS` | 10 | 8–12 | PT size 2^bits × 9 bits. Bigger means fewer signature collisions | Tune (storage) |
| `SPP_SIG_SHIFT` | 3 | 2–4 | History length ≈ SIG_BITS / SHIFT strides. 10/3 gives 3 full strides plus part of a 4th, which the period-5 example needs | Tune |
| `SPP_PT_CONF_BITS` | 2 | 2–3 | PT counter width | Leave |
| `SPP_PT_CONF_THRESHOLD` | 2 | 1–max | Confidence for the first non-uniform prefetch | Tune |
| `SPP_PT_LOOKAHEAD_CONF` | 2 | 1–max | Confidence for deeper lookahead steps. Raise it if pattern prefetches are inaccurate | Tune |
| `SPP_EPOCH_ACCESSES` | 1024 | 256–8192 | Responsiveness vs. noise of the accuracy estimate | Tune |
| `SPP_MIN_EPOCH_ISSUED` | 16 | 8–128 | Minimum samples before a decision | Leave |
| `SPP_ACC_CTR_BITS` | 16 | 12–16 | Counter width (storage only) | Fixed |
| `SPP_ACC_HIGH / MID / LOW / TOXIC` | 0.75 / 0.50 / 0.25 / 0.10 | 0–1, decreasing | Accuracy ranges (deliverable) | **Tune** |
| `SPP_DEGREE_HIGH / MID / LOW / POOR / TOXIC` | 4 / 3 / 2 / 1 / 0 | 0–8 | Degree per range (deliverable) | **Tune** |
| `SPP_INIT_DEGREE` | 2 | 1–4 | Degree before the first decision. Only affects warmup | Leave |
| `SPP_FILTER_INDEX_BITS` | 8 | 6–10 | Filter reach. It must cover the prefetch-to-use distance | Tune |
| `SPP_FILTER_TAG_BITS` | 8 | 4–16 | Aliasing makes accuracy slightly optimistic | Leave |
| `SPP_L1_FILL_MSHR_LIMIT` | MSHR/2 | 0–MSHR | Fill L1 vs. L2 under MSHR pressure (inherited from `ip_stride`) | Leave |

## Metrics

From ChampSim's ROI output (`scripts/parse_results.py`): IPC; speedup = IPC / IPC(`no lru`); L1D load MPKI; accuracy = `USEFUL LOAD PREFETCHES / PREFETCH ISSUED TO LOWER LEVEL` (ChampSim's own definition); coverage = (L1D load misses(baseline) − L1D load misses(SPP)) / L1D load misses(baseline). The SPP's filter accuracy and its uniform vs. pattern split are also printed and parsed, which helps explain the per-trace results in the report.
