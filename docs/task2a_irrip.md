# Task 2A: Instruction-conscious RRIP (I-RRIP) at L2C

Code: [`replacement/irrip.l2c_repl`](../replacement/irrip.l2c_repl). Build with `./build_champsim.sh no irrip irrip`.

## Plumbing: the `isInstruction` parameter

`update_replacement_state` is a member-function pointer shared by every cache, so the new parameter has to appear everywhere the pointer's type does:

- `inc/cache.h`: the pointer type and all nine `*_update_replacement_state` declarations take `uint8_t isInstruction`.
- `replacement/*_replacement.cc` and every `replacement/lru.*_repl`: definitions updated. `rrip.l2c_repl` already had the new signature and now builds.
- `src/cache.cc`: all 8 call sites pass the packet's `instruction` flag (`MSHR.entry[..]` on fills, `RQ.entry[..]` on read hits, `PQ.entry[..]` on prefetch hits, `WQ.entry[..]` on writebacks, which is always 0). This includes the two `DTLB_PB` calls compiled only under `PUSH_DTLB_PB`.
- `src/ooo_cpu.cc`: the 3 BTB calls pass 0.
- `src/main.cc`: single-core runs now call `L2C.l2c_replacement_final_stats()`. Upstream only called it for multi-core, so the policy's own counters were never printed.

The policy treats a line as an instruction line only for `LOAD`/`PREFETCH` packets with `instruction == 1`. That is the same rule `fill_cache()` uses for `block.instruction` and the rule behind ChampSim's "L2C INSTRUCTION LOAD MPKI", so the policy and the metric agree. Page-walk (translation) lines count as data.

## Design and rationale (insertion, promotion, eviction)

Starting point: SRRIP in `rrip.l2c_repl` (2-bit RRPV, insert 2, hit → 0, evict at 3).

**Insertion.** Instruction fills go in at RRPV 1, data fills at 2.
Instruction lines that miss in L1I and come from L2 are re-fetched every time the same code runs again (loops, hot functions, shared libraries), so near re-reference is the right prediction for them. Data keeps exactly the SRRIP insertion. The bias comes from moving instructions closer, not from punishing data. Inserting data at 3, which you suggested as the aggressive option, turns every data fill into an immediate eviction candidate, which hurts any data with reuse at L2 distance. Inserting instructions at 0 is the stronger option and can be tried through `IRRIP_INSERT_INSTR=0`. I did not make it the default because, with a code footprint bigger than L2, never-reused code would then sit at the same priority as just-hit lines.

**Promotion.** An instruction hit → 0. A data hit moves one step (RRPV − 1). Writeback hits do not promote.
A single data hit no longer gives a data line the same protection as an instruction hit. Data that is genuinely hot still reaches 0 after two hits, so frequently reused data (stack, hot structures) is not starved. A writeback is the L1D evicting a dirty line, not the core reading it. SRRIP's promotion on writeback hits protects lines the L1D has just declared cold.

**Eviction.** The set is aged exactly as in SRRIP. Among the lines at RRPV 3, a data line is chosen before an instruction line (`IRRIP_EVICT_MODE 1`).
Insertion and promotion already make data reach 3 sooner. The tie-break covers the remaining case where both kinds reach 3 together. Without it, the first way at 3 is evicted, which is arbitrary with respect to type. This is a zero-cost change: the per-line instruction bit already exists in `BLOCK`. Mode 2 (a data line at 2 beats an instruction line at 3) is stronger and should be a sweep point. It can hold dead code too long, so it is not the default.

Why this is the best default: each of the three changes makes instructions win when the two kinds are otherwise tied. None of them makes data worse than SRRIP by construction, and data still has a path to full protection. The aim is to lower L2 instruction MPKI without raising data MPKI. Raising data MPKI is the failure the dynamic version in Task 2B guards against.

Storage: 2-bit RRPV and 1 instruction bit per line = 8,192 × 3 = 24,576 bits (SRRIP needs 16,384).

## Tunable parameters

| Knob | Default | Range | Trade-off | Class |
|---|---|---|---|---|
| `IRRIP_RRPV_BITS` | 2 | 2–3 | M=3 gives finer priorities but takes longer to age | Leave (keep equal to the RRIP reference) |
| `IRRIP_INSERT_INSTR` | 1 | 0–max | Lower means stronger instruction priority | **Tune** |
| `IRRIP_INSERT_DATA` | max−1 | max−1..max | max means data are inserted as eviction candidates | Tune |
| `IRRIP_INSERT_WRITEBACK` | max−1 | max−1..max | Writeback allocations | Leave |
| `IRRIP_HIT_INSTR_RRPV` | 0 | 0–1 | | Leave |
| `IRRIP_DATA_HIT_MODE` | 1 | 0 / 1 | 0 = SRRIP-HP (data hit → 0), 1 = step promotion | **Tune** |
| `IRRIP_DATA_HIT_STEP` | 1 | 1–max | Step size in mode 1 | Tune |
| `IRRIP_PROMOTE_ON_WB_HIT` | 0 | 0 / 1 | 1 restores `rrip.l2c_repl` behaviour | Leave |
| `IRRIP_EVICT_MODE` | 1 | 0 / 1 / 2 | 0 = plain SRRIP, 1 = data first at max, 2 = data at max−1 beats instruction at max | **Tune** |

Report metrics: IPC, speedup vs `no lru`, L2C load MPKI, L2C instruction load MPKI and L2C data load MPKI (`L2C DATA LOAD MPKI` / `L2C INSTRUCTION LOAD MPKI` lines), compared against `rrip` and the LRU baseline.
