#!/bin/bash
# ab-status: one screen of every lane's state, including money spent by the run in flight
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/logs/ab-rlm-agentic
PY=${PY:-/Users/apytel/Projects/FreeCadCopilot/.venv/bin/python}

for M in haiku sonnet opus; do
  S=$OUT/$M/state.json
  [ -f "$S" ] || { echo "$M: no state"; continue; }
  if pgrep -f "ab_lane.py --model $M " >/dev/null; then ALIVE=1; else ALIVE=0; fi
  python3 - "$S" "$M" "$ALIVE" <<'EOF'
import json, os, sys, time
state = json.load(open(sys.argv[1]))
model, alive = sys.argv[2], sys.argv[3] == "1"
flight = 0.0
for run in state["runs"].values():
    if run["status"] == "running" and os.path.exists(run["log"]):
        for line in open(run["log"]):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event["kind"] == "cost":
                flight += event["data"].get("usd") or 0.0
cap = state.get("lane_cap_usd") or state["budget_usd"]
print("%-7s $%.2f/%.0f lane%s  (ceiling $%.0f/run)  %s" % (
      model, state["usd"], cap,
      (" +$%.2f in flight" % flight) if flight else "",
      state["budget_usd"], "RUNNING" if alive else "STOPPED"))
for key, run in sorted(state["runs"].items()):
    age = ""
    if run["status"] == "running" and os.path.exists(run["log"]):
        age = " last event %.0fs ago" % (time.time() - os.path.getmtime(run["log"]))
    print("   %-10s %-8s steps=%-3s $%.2f %5.0fs %s%s" % (
        key, run["status"], run.get("steps_applied", "-"), run.get("usd") or 0,
        run.get("seconds") or 0, run.get("transient") or "", age))
EOF
done
