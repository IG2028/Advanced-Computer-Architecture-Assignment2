#!/bin/bash
# Shared list of simulator configurations for build_all.sh / run_all.sh.
# Format: <config name>:<L1D prefetcher>:<L2C replacement>
# The config name is the binary suffix and the results/ sub-directory.
CONFIGS=(
  "baseline:no:lru"                        # speedup / coverage baseline (no prefetching, LRU L2)
  "ip_stride:ip_stride:lru"                # Task 1A reference
  "spp:spp:lru"                            # Task 1B  (also "SPP alone" for Task 3)
  "rrip:no:rrip"                           # SRRIP reference for Task 2
  "irrip:no:irrip"                         # Task 2A
  "dyn_irrip:no:dyn_irrip"                 # Task 2B  (default threshold)
  "spp_dyn_irrip:spp:dyn_irrip"            # Task 3 ablation: SPP + dynamic I-RRIP without prefetch awareness
  "spp_pf_dyn_irrip:spp:pf_dyn_irrip"      # Task 3
)

WARMUP=${WARMUP:-25000000}
SIM=${SIM:-25000000}
TRACE_DIR=${TRACE_DIR:-../traces}
RESULTS_DIR=${RESULTS_DIR:-results}
JOBS=${JOBS:-$(nproc 2>/dev/null || echo 4)}

# binary path produced by build_champsim.sh for <l1d pref> <l2c repl> <name>
binary_for() {
  echo "bin/hashed_perceptron-no-$1-no-no-no-no-no-lru-lru-lru-$2-lru-lru-lru-lru-1core-$3"
}

# look up one config line by name
config_line() {
  local c
  for c in "${CONFIGS[@]}"; do
    [[ "${c%%:*}" == "$1" ]] && { echo "$c"; return 0; }
  done
  return 1
}
