#!/bin/bash
# Build every configuration in scripts/configs.sh (or only the names given).
#   scripts/build_all.sh                 # all
#   scripts/build_all.sh spp irrip       # some
# Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/configs.sh

names=("$@")
if [ ${#names[@]} -eq 0 ]; then
  for c in "${CONFIGS[@]}"; do names+=("${c%%:*}"); done
fi

for n in "${names[@]}"; do
  line=$(config_line "$n") || { echo "unknown config $n" >&2; exit 1; }
  IFS=: read -r name pref repl <<< "$line"
  echo "=== building $name ($pref + $repl)"
  ./build_champsim.sh "$pref" "$repl" "$name" > "build_$name.log" 2>&1 || { echo "build of $name FAILED, see build_$name.log" >&2; exit 1; }
  test -x "$(binary_for "$pref" "$repl" "$name")" || { echo "binary for $name missing" >&2; exit 1; }
  rm -f "build_$name.log"
done
echo "all builds done"
