#!/bin/bash
# ab-go: (re)start one lane, or all three, picking up exactly where the state left off
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/logs/ab-rlm-agentic
PY=${PY:-/Users/apytel/Projects/FreeCadCopilot/.venv/bin/python}

# the lane imports `core` from the checkout it runs in, so it runs from the one
# under test -- dev has no TRAP_APPROVED and the lane dies on the import
WT=${WT:-/Users/apytel/Projects/FreeCadCopilot-wt/rlm-version}
cd "$WT" || exit 1
for M in ${@:-haiku sonnet opus}; do
  if pgrep -f "ab_lane.py --model $M " >/dev/null; then
    echo "$M already running, left alone"
    continue
  fi
  nohup $PY "$ROOT/tools/ab_lane.py" --model $M --effort medium \
    --intent "Build the Phase 1 model from this drawing." \
    --task mm25=tests/modelmania/mm25-p1-1.pdf \
    --task mm24=tests/modelmania/model-mania-2024.pdf \
    --task mm23=tests/modelmania/model-mania_-in-person-2023.pdf \
    --budget-usd 20 --lane-cap-usd 60 --retries 3 --resume \
    --out $OUT/$M >> $OUT/$M.log 2>&1 &
  echo "$(date '+%H:%M:%S') started $M pid $!" | tee -a $OUT/relaunch.log
done
