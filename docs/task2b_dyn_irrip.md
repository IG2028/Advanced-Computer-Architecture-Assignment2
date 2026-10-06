# Task 2B: Dynamic I-RRIP (set dueling)

Code: [`replacement/dyn_irrip.l2c_repl`](../replacement/dyn_irrip.l2c_repl) with `PREFETCH_AWARE 0` (the default). Build with `./build_champsim.sh no dyn_irrip dyn_irrip`.

## Design

**Two policies.** SRRIP (as in `rrip.l2c_repl`, but writeback hits do not promote) and the Task 2A I-RRIP.

**Leader sets.** 32 SRRIP leaders and 32 I-RRIP leaders out of 1,024 sets (6.25%). The other 992 sets follow a 1-bit policy selector. The sets are split into 32 regions of 32 consecutive sets. Region *r* has its SRRIP leader at offset *r* and its I-RRIP leader at offset 31−*r* (complement select, Qureshi et al. ISCA'07). Leaders are therefore spread across the whole index range, and a hot region of the address space cannot bias one kind of leader. In hardware this is a comparator on index bits and needs no storage.

Why 32: set-dueling studies show 16–64 leaders per policy is enough to estimate a policy's miss rate. With 32, each leader group sees about 1/32 of L2 demand traffic, roughly 500 samples per group per 16K-access epoch. That is enough to estimate a ratio to a few percent while the leaders, which are stuck on a possibly wrong policy, make up only 6% of the cache. Sweep 16 and 64 to show the trade-off (`DIRRIP_LEADER_SETS`).

**Counters.** For each leader kind there are four saturating 16-bit counters: instruction accesses, total accesses, instruction misses and total misses. Only demand packets count (LOAD, RFO, LOAD_TRANSLATION). Writebacks and prefetches do not, since the core is not waiting on them. An access is a hit or a fill; a miss is a fill (one per MSHR, so merged requests are not double counted).

**Decision**, every 16,384 L2 demand accesses (counters are halved afterwards so the decision tracks program phases):

```
R      = (A_instr + w·M_instr) / (A_total + w·M_total)       on SRRIP leaders, w = 1  -> the spec's formula
want   = R > T·(1+h)   when the followers run SRRIP           T = threshold, h = 0.05
         R ≥ T·(1−h)   when the followers run I-RRIP          (relative hysteresis band, no flip-flopping)
cost   = c·M_instr + M_data,  c = 2                           per leader kind
guard  = cost(I-RRIP leaders) ≤ cost(SRRIP leaders)·(1 + 0.02)
followers run I-RRIP  iff  want AND guard                     (DIRRIP_DECISION_MODE 2)
```

- The ratio is exactly the spec hint (`w = 1`), measured on leader sets that always run RRIP as the spec asks. It answers "is instruction traffic a large enough share of L2 traffic to be worth prioritising?"
- The guard answers the question the spec motivates Task 2B with: "is prioritising instructions currently costing more data misses than it saves?" It compares the two leader groups directly. An instruction miss is weighted 2× because it stalls fetch, while out-of-order execution hides part of a data miss's latency.
- Mode 0 (ratio only) and mode 1 (guard only) are both available. Run the threshold sweep in mode 0 if you want the pure effect of the threshold (`MODE=0 scripts/run_sweep.sh`).

**Sensitivity study.** `scripts/run_sweep.sh` builds and runs five thresholds (0.05, 0.10, 0.15, 0.25, 0.40) and `scripts/plot_results.py` draws the bars and the threshold line charts. Each run also prints `DIRRIP mean_ratio` and `irrip_fraction`. If the mean ratios of the traces all fall on one side of the swept values, re-centre the five thresholds around the observed ratios so the study shows the switch actually happening.

Storage at defaults: RRPV 16,384 + instruction bit 8,192 + counters 8×16 + epoch counter 15 + selector 1 = 24,720 bits.

## What I took from your Task 2B ideas

| Idea | Verdict |
|---|---|
| Dedicated I-RRIP and normal-RRIP leader sets, followers follow the winner | **Kept**. It is the spec's set dueling, and it makes the guard possible. |
| Instruction-miss weight α (2–4) in a cost function | **Kept** as `DIRRIP_INSTR_MISS_COST = 2`. Deriving α from cycles per miss is not possible inside the replacement policy (it never sees stall cycles), so it is a sweepable constant. Run 2 and 4. |
| Hysteresis guardband θ | **Kept**, as a relative band on the threshold (`h`) plus a margin on the duel. Your absolute form, θ·α·M_inst,total, changes scale with traffic; a relative one does not. |
| Epoch, then clear the counters | **Kept**, but the counters are **halved** instead of cleared, so one noisy epoch cannot flip the policy. |
| Global counters instead of dedicated sets (IPI over the whole cache) | **Dropped.** The spec requires leader sets running RRIP, and global counters measure the mix of both policies, so they cannot tell which one is better. The IPI form `(α·M + A)/(α·M_total + A_total)` survives as `R`, with its weight `w` exposed as a knob. |
| Gating on L2 instruction miss rate (< 5% keep RRIP, > 15% switch) | **Dropped.** It adds two more thresholds to tune. The duel guard covers "instructions are already happy" more directly: when I-RRIP does not lower the weighted miss cost, it is not used. |

## Tunable parameters

| Knob | Default | Range | Trade-off | Class |
|---|---|---|---|---|
| `DIRRIP_THRESHOLD` | 0.25 | 0–1 | Ratio threshold; this is the knob for the 5-point study | **Tune** |
| `DIRRIP_DECISION_MODE` | 2 | 0 / 1 / 2 | Ratio / duel / both | **Tune** |
| `DIRRIP_LEADER_SETS` | 32 | 8–128 (power of 2, ≤ sets/2) | Sampling accuracy vs. sets stuck on the wrong policy | **Tune** |
| `DIRRIP_EPOCH_ACCESSES` | 16384 | 4K–128K | Reaction speed vs. noise | Tune |
| `DIRRIP_HYSTERESIS` | 0.05 | 0–0.2 | Stability vs. reaction near the threshold | Tune |
| `DIRRIP_RATIO_MISS_WEIGHT` | 1.0 | 1–4 | 1 = spec formula; higher weights misses over accesses | Tune |
| `DIRRIP_INSTR_MISS_COST` | 2.0 | 1–4 | α in the duel cost | Tune |
| `DIRRIP_GUARD_MARGIN` | 0.02 | 0–0.1 | How much worse the I-RRIP leaders may be in the duel | Leave |
| `DIRRIP_MIN_SAMPLES` | 64 | 16–512 | Skip decisions on too little traffic | Leave |
| `DIRRIP_CTR_BITS` | 16 | 12–20 | Storage only | Fixed |
| `DIRRIP_INIT_IRRIP` | 0 | 0 / 1 | Starting policy (warmup only) | Leave |
| `DIRRIP_INSERT_INSTR`, `DIRRIP_INSERT_DATA`, `DIRRIP_DATA_HIT_STEP`, `DIRRIP_EVICT_PREFER_DATA` | 1, 2, 1, 1 | as Task 2A | The I-RRIP half of the duel; keep them equal to Task 2A | Fixed by 2A |
| `DIRRIP_SRRIP_INSERT`, `DIRRIP_INSERT_WRITEBACK`, `DIRRIP_PROMOTE_ON_WB_HIT` | 2, 2, 0 | | The SRRIP half | Leave |
