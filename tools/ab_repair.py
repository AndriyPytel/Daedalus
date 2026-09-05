#!/usr/bin/env python
"""
Split a log that holds more than one run and keep the one that counted.

`EventStream.log_to` appends, so every retry and every --resume wrote its run
into the previous run's file. `measure` reads a file as one run, so a merged
log inflates Steps and dollars with an attempt that was thrown away. The last
run in the file is the one the state records; the earlier ones are kept beside
it as evidence.
"""
import json
import os
import sys

sys.path.insert(0, os.path.realpath(os.getcwd()))
from core.measure import measure


def segments(path):
    found, current = [], []
    for line in open(path, encoding="utf-8"):
        try:
            kind = json.loads(line).get("kind")
        except ValueError:
            continue
        if kind == "intent" and current:
            found.append(current)
            current = []
        current.append(line)
    if current:
        found.append(current)
    return found


def _kept_cost(lines):
    """Each line parsed once — `kept` used to call `json.loads` a second time
    per line just to re-read the `kind` it already had (FCC-287d)."""
    total = 0.0
    for line in lines:
        entry = json.loads(line)
        if entry["kind"] == "cost":
            total += entry["data"].get("usd") or 0
    return total


def main(argv):
    for stem in argv:
        log = stem + ".jsonl"
        parts = segments(log)
        if len(parts) < 2:
            print("%s: single run, left alone" % os.path.basename(log))
            continue
        index = 1
        for part in parts[:-1]:
            while os.path.exists("%s.attempt%d.jsonl" % (stem, index)):
                index += 1
            with open("%s.attempt%d.jsonl" % (stem, index), "w", encoding="utf-8") as handle:
                handle.writelines(part)
            index += 1
        with open(log, "w", encoding="utf-8") as handle:
            handle.writelines(parts[-1])
        reading = measure(log)
        with open(stem + ".measure.json", "w", encoding="utf-8") as handle:
            json.dump({f: getattr(reading, f) for f in reading.__dataclass_fields__},
                      handle, indent=2, default=list)
        kept = _kept_cost(parts[-1])
        print("%s: %d runs -> kept the last, $%.2f, %d line(s)"
              % (os.path.basename(log), len(parts), kept, len(parts[-1])))


if __name__ == "__main__":
    main(sys.argv[1:])
