#!/usr/bin/env python
"""
Assemble a CADGenBench submission bundle from a milestone's already-produced
`output.step` files -- never uploads it, never authenticates. Mirrors the
zip contract `docs/benchmark/submission.md` documents and
`cadgenbench.baseline.package` (Apache-2.0) implements: a root `meta.json`
plus one folder per fixture, present or missing, since the Space checks the
zip against the exact fixture set before it will score anything.

    .venv/bin/python src/tools/cadgenbench_bundle.py bundle \
        --run-dir manual/cadgenbench/milestone-1 --out /tmp/milestone-1.zip

    .venv/bin/python src/tools/cadgenbench_bundle.py record \
        --path manual/cadgenbench/milestones.jsonl --milestone milestone-1 \
        --local-score 0.71 --leaderboard-score 0.42
"""
import argparse
import getpass
import glob
import json
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_MANIFEST = os.path.join(ROOT, "manual", "cadgenbench", "generation_fixtures.json")
CANDIDATE_NAMES = ("output.step", "output.stp")
INCOMPARABILITY_NOTE = (
    "Local score = 0.7*shape_similarity + 0.3*topology over our Exemplars; "
    "leaderboard score adds interface_match at 0.4 over private ground truth. "
    "Never compared to each other."
)


def generation_fixtures(manifest_path=DEFAULT_MANIFEST):
    """The 49 CADGenBench generation-task fixture ids, from the cached manifest.

    The manifest was fetched once from the public, unauthenticated
    `cadgenbench-data` dataset listing API and is read here with no network
    call, so bundling and its tests stay offline.
    """
    with open(manifest_path, encoding="utf-8") as handle:
        return json.load(handle)["fixtures"]


def editing_fixtures(manifest_path=DEFAULT_MANIFEST):
    """The 32 CADGenBench editing-task fixture ids."""
    with open(manifest_path, encoding="utf-8") as handle:
        return json.load(handle)["editing_fixtures"]


def _candidate_for(run_dir, fixture):
    """The fixture's `output.step` folder, or else the latest `<fixture>.<stamp>.step` a lane run saved."""
    for name in CANDIDATE_NAMES:
        candidate = os.path.join(run_dir, fixture, name)
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    saved = sorted(path for path in glob.glob(os.path.join(run_dir, fixture + ".*.step"))
                   if os.path.getsize(path) > 0)
    return saved[-1] if saved else None


def plan_bundle(run_dir, fixtures):
    """One entry per *fixtures* id: whether *run_dir* has a candidate for it.

    This is the inspectable report -- printed before `write_bundle` touches
    disk, so a human sees exactly what would be sent before anything is.
    """
    plan = []
    for fixture in fixtures:
        candidate = _candidate_for(run_dir, fixture)
        plan.append({
            "fixture": fixture,
            "candidate": candidate,
            "status": "found" if candidate else "missing",
        })
    return plan


def _print_plan(plan, run_dir):
    print("bundle plan for %s" % run_dir)
    for entry in plan:
        print("  %-6s %-7s %s" % (entry["fixture"], entry["status"], entry["candidate"] or ""))
    found = sum(1 for entry in plan if entry["status"] == "found")
    print("%d/%d fixtures found, %d missing" % (found, len(plan), len(plan) - found))


def write_bundle(run_dir, fixtures, out_path, submitter=None, submission_name=None,
                 agent_url=None, notes=None, agree=False):
    """Write the submission zip for *fixtures* found under *run_dir* to *out_path*.

    Every fixture in *fixtures* gets a folder entry in the zip whether or not
    a candidate was found for it, matching the "no directory disappears"
    contract `cadgenbench.baseline.package._discover_fixture_entries` relies
    on; a fixture with no entry at all is what fails the Space's exact
    fixture-set check, not a fixture recorded as missing.
    """
    plan = plan_bundle(run_dir, fixtures)
    _print_plan(plan, run_dir)

    meta = {
        "submitter_name": submitter or getpass.getuser(),
        "submission_name": submission_name or os.path.basename(os.path.normpath(run_dir)),
        "agent_url": agent_url,
        "notes": notes,
        "agree_to_publish": bool(agree),
    }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2) + "\n")
        for entry in plan:
            zf.writestr("%s/" % entry["fixture"], "")
            if entry["candidate"]:
                name = os.path.basename(entry["candidate"])
                name = name if name in CANDIDATE_NAMES else "output.step"
                zf.write(entry["candidate"], arcname="%s/%s" % (entry["fixture"], name))

    missing = [entry["fixture"] for entry in plan if entry["status"] == "missing"]
    report = {
        "out_path": out_path,
        "n_fixtures": len(plan),
        "n_found": len(plan) - len(missing),
        "n_missing": len(missing),
        "missing": missing,
        "agree_to_publish": meta["agree_to_publish"],
    }
    print("wrote %s (%d/%d fixtures, agree_to_publish=%s)"
          % (out_path, report["n_found"], report["n_fixtures"], report["agree_to_publish"]))
    if not agree:
        print("NOTE: agree_to_publish=false -- the Space will reject this zip "
              "until a human re-runs with --agree.")
    return report


def record_milestone(path, milestone, local_score, leaderboard_score):
    """Append one line recording *milestone*'s two scores, always with the fixed caveat.

    `INCOMPARABILITY_NOTE` is not a parameter: nothing that calls this can
    accidentally record the pair without the words that stop someone
    subtracting one from the other.
    """
    record = {
        "milestone": milestone,
        "local_score": local_score,
        "leaderboard_score": leaderboard_score,
        "note": INCOMPARABILITY_NOTE,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    bundle_cmd = sub.add_parser("bundle", help="Assemble the submission zip; never uploads it.")
    bundle_cmd.add_argument("--run-dir", required=True)
    bundle_cmd.add_argument("--out", default=None)
    bundle_cmd.add_argument("--manifest", default=DEFAULT_MANIFEST)
    bundle_cmd.add_argument("--submitter", default=None)
    bundle_cmd.add_argument("--name", dest="submission_name", default=None)
    bundle_cmd.add_argument("--agent-url", default=None)
    bundle_cmd.add_argument("--notes", default=None)
    bundle_cmd.add_argument("--agree", action="store_true",
                            help="Set agree_to_publish=true. Omitted => false stub.")

    record_cmd = sub.add_parser("record", help="Record a milestone's two scores side by side.")
    record_cmd.add_argument("--path", required=True)
    record_cmd.add_argument("--milestone", required=True)
    record_cmd.add_argument("--local-score", type=float, required=True)
    record_cmd.add_argument("--leaderboard-score", type=float, required=True)

    args = parser.parse_args()

    if args.command == "bundle":
        out = args.out or (os.path.normpath(args.run_dir) + ".zip")
        fixtures = generation_fixtures(args.manifest) + editing_fixtures(args.manifest)
        write_bundle(args.run_dir, fixtures, out, submitter=args.submitter,
                    submission_name=args.submission_name, agent_url=args.agent_url,
                    notes=args.notes, agree=args.agree)
        return 0

    record = record_milestone(args.path, args.milestone, args.local_score,
                              args.leaderboard_score)
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
