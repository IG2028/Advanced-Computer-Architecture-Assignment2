#!/bin/bash
# Run configurations over every trace in $TRACE_DIR, JOBS at a time.
#   TRACE_DIR=../traces JOBS=8 scripts/run_all.sh              # all configs
#   scripts/run_all.sh spp spp_pf_dyn_irrip                    # some configs
# Output: $RESULTS_DIR/<config>/<trace>.txt. Finished runs are skipped, so the
# script can be re-run after an interruption. Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/configs.sh

names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  for c in "${CONFIGS[@]}"; do names+=("${c%%:*}"); done
fi

shopt -s nullglob
traces=("$TRACE_DIR"/*.champsimtrace.xz "$TRACE_DIR"/*.champsimtrace.gz "$TRACE_DIR"/*.trace.xz "$TRACE_DIR"/*.trace.gz)
if [ ${#traces[@]} -eq 0 ]; then
  echo "no traces found in $TRACE_DIR" >&2; exit 1
fi

jobfile=$(mktemp)
for n in "${names[@]}"; do
  # names from the sweep are "<prefix>:<pref>:<repl>" passed via SWEEP_LINES; normal names come from configs.sh
  line=$(config_line "$n" || true)
  [ -z "$line" ] && line=$(printf '%s\n' "${SWEEP_LINES:-}" | grep "^$n:" || true)
  [ -z "$line" ] && { echo "unknown config $n" >&2; exit 1; }
  IFS=: read -r name pref repl <<< "$line"
  bin=$(binary_for "$pref" "$repl" "$name")
  [ -x "$bin" ] || { echo "missing binary $bin (run scripts/build_all.sh $name)" >&2; exit 1; }
  mkdir -p "$RESULTS_DIR/$name"
  for t in "${traces[@]}"; do
    tn=$(basename "$t"); tn=${tn%%.*}
    out="$RESULTS_DIR/$name/$tn.txt"
    if [ -f "$out" ] && grep -q "ChampSim completed all CPUs" "$out"; then continue; fi
    echo "$bin -warmup_instructions $WARMUP -simulation_instructions $SIM -traces $t > $out 2>&1" >> "$jobfile"
  done
done

njobs=$(wc -l < "$jobfile")
echo "running $njobs simulations with $JOBS parallel jobs"
if [ "$njobs" -gt 0 ]; then
  tr '\n' '\0' < "$jobfile" | xargs -0 -P "$JOBS" -I{} bash -c '{} || echo "FAILED: {}" >&2'
fi
rm -f "$jobfile"
echo "done; parse with: python3 scripts/parse_results.py"
