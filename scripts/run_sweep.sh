#!/bin/bash
# Task 2B sensitivity study: build and run dyn_irrip with 5 thresholds.
#   scripts/run_sweep.sh                                   # thresholds 0.05 0.10 0.15 0.25 0.40
#   THRESHOLDS="0.1 0.2 0.3 0.4 0.5" scripts/run_sweep.sh
#   MODE=0 scripts/run_sweep.sh        # ratio-only decision (pure threshold effect, exactly the spec hint)
#   PREF=spp REPL=pf_dyn_irrip scripts/run_sweep.sh        # same sweep for Task 3
# Each point becomes config "dynT_<threshold>" (or "<REPL>T_<threshold>") under results/.
# Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/configs.sh

THRESHOLDS=${THRESHOLDS:-"0.05 0.10 0.15 0.25 0.40"}
PREF=${PREF:-no}
REPL=${REPL:-dyn_irrip}
PREFIX=${PREFIX:-$([ "$REPL" = dyn_irrip ] && echo dyn || echo "$REPL")}

lines=""
names=()
for t in $THRESHOLDS; do
  name="${PREFIX}T_${t}"
  variant="${REPL}_t${t/./}"
  kv=("DIRRIP_THRESHOLD=$t")
  [ -n "${MODE:-}" ] && kv+=("DIRRIP_DECISION_MODE=$MODE")
  scripts/make_variant.sh "replacement/$REPL.l2c_repl" "$variant" "${kv[@]}" > /dev/null
  echo "=== building $name"
  ./build_champsim.sh "$PREF" "$variant" "$name" > "build_$name.log" 2>&1 || { echo "build of $name FAILED, see build_$name.log" >&2; exit 1; }
  rm -f "build_$name.log"
  lines+="$name:$PREF:$variant"$'\n'
  names+=("$name")
done

SWEEP_LINES="$lines" scripts/run_all.sh "${names[@]}"
