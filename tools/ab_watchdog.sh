#!/bin/bash
# ab-watchdog: a lane whose open run has gone quiet is hung, not thinking -- restart it
#
# Closing the lid kills the socket under the `claude` child, and nothing upstream
# notices: one opus child sat in state S for 101 minutes after the wake and the
# lane waited on it forever. A run writes a `thought` every minute or so while it
# is alive, so silence that long is a hang. The run's state stays `running`, so a
# plain --resume re-runs exactly that pair and keeps every banked one.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/logs/ab-rlm-agentic
PY=${PY:-/Users/apytel/Projects/FreeCadCopilot/.venv/bin/python}
QUIET=1800

while true; do
  for M in haiku sonnet opus; do
    S=$OUT/$M/state.json
    [ -f "$S" ] || continue
    pgrep -f "ab_lane.py --model $M " >/dev/null || continue
    STALE=$(python3 -c "
import json, os, time
state = json.load(open('$S'))
for key, run in state['runs'].items():
    if run.get('status') == 'running' and os.path.exists(run.get('log', '')):
        quiet = time.time() - os.path.getmtime(run['log'])
        if quiet > $QUIET:
            print('%s %.0f' % (key, quiet))
" 2>/dev/null)
    if [ -n "$STALE" ]; then
      echo "$(date '+%H:%M:%S') $M hung on $STALE s of silence, restarting" >> $OUT/watchdog.log
      pkill -f "ab_lane.py --model $M " ; sleep 5
      pkill -9 -f "ab_lane.py --model $M " 2>/dev/null ; sleep 3
      "$ROOT/tools/ab_go.sh" $M >> $OUT/watchdog.log 2>&1
    fi
  done
  sleep 60
done
