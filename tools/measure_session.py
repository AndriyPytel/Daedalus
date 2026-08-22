#!/usr/bin/env python
"""
Report a Session log's measurement, or compare a baseline set against an
experiment set, without a REPL.

    tools/measure_session.py logs/sessions/one.jsonl logs/sessions/two.jsonl
    tools/measure_session.py --baseline logs/main/*.jsonl \\
        --experiment logs/rlm-version/*.jsonl \\
        --baseline-label main --experiment-label rlm-version

The first form prints one `core.measure.Measurement` per log, in the order
given. The second prints the Markdown comparison table `core.measure.compare`
builds, ready to paste into a ticket comment.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.measure import compare, measure


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="*", help="Session logs to measure, one report each")
    parser.add_argument("--baseline", nargs="+", default=[], help="Baseline Session logs")
    parser.add_argument("--experiment", nargs="+", default=[], help="Experiment Session logs")
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--experiment-label", default="experiment")
    args = parser.parse_args()

    if args.baseline or args.experiment:
        print(compare(args.baseline, args.experiment,
                      args.baseline_label, args.experiment_label))
        return 0

    if not args.logs:
        parser.print_help()
        return 2

    for path in args.logs:
        result = measure(path)
        print(path)
        for field in result.__dataclass_fields__:
            print("  %s: %s" % (field, getattr(result, field)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
