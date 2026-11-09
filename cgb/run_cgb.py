#!/usr/bin/env python3
"""Re-run the Daedalus CADGenBench arms on this machine: check, set up, run, pack. Stdlib only."""
import argparse
import datetime
import glob
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HF = "HuggingAI4Engineering/cadgenbench-data"
INTENT = "Reproduce the geometry as accurately as possible from the drawing."
PINS = {"httpx": "httpx==0.28.1", "litellm": "litellm==1.104.0", "pymupdf": "pymupdf==1.28.2",
        "yaml": "pyyaml==6.0.3", "PIL": "pillow==12.0.0", "numpy": "numpy"}
ARMS = {
    "p17": ("p17", "pipeline", {}),
    "p17-whole": ("p17", "pipeline", {"DAEDALUS_DESIGN_WHOLE_SHEET": "1"}),
    "a4h": (None, "reader", {}),
    "a4h-whole": (None, "reader", {"DAEDALUS_READER_WHOLE_SHEET": "1"}),
}


def say(text):
    print(f"{datetime.datetime.now():%F %T} {text}", flush=True)


def die(text):
    sys.exit(f"ERROR: {text}")


def sh(cmd, env=None, cwd=None):
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)


def platform_name():
    if sys.platform == "darwin":
        return "macOS"
    return "WSL" if "microsoft" in platform.release().lower() else "Linux"


def freecad_layout(root):
    for sub, lib in (("usr", "usr/lib"), ("Contents/Resources", "Contents/Resources/lib"), ("", "lib")):
        bin_dir = os.path.join(root, sub, "bin")
        if os.path.exists(os.path.join(bin_dir, "freecadcmd")) and os.path.exists(os.path.join(bin_dir, "python")):
            return bin_dir, os.path.join(root, lib)
    return None


def find_freecad(explicit):
    home = os.path.expanduser("~")
    roots = [explicit, os.environ.get("FREECAD_DIR"), f"{home}/freecad", f"{home}/squashfs-root", "/opt/freecad",
             "/Applications/FreeCAD.app"]
    for root in filter(None, roots):
        if freecad_layout(root):
            return (root, *freecad_layout(root))
    die("FreeCAD not found. Extract the FreeCAD 1.1 Linux AppImage "
        "(./FreeCAD*.AppImage --appimage-extract; mv squashfs-root ~/freecad) or pass --freecad DIR "
        "(DIR holds usr/bin/freecadcmd and usr/bin/python).")


def run_env(bin_dir, lib, claude, extra=()):
    env = dict(os.environ, FREECAD_CMD=os.path.join(bin_dir, "freecadcmd"), FREECAD_LIB=lib,
               CLAUDE_CMD=claude, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [os.path.join(HERE, ".deps"), lib, os.environ.get("PYTHONPATH")]))
    if sys.platform != "darwin":
        env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [lib, os.environ.get("LD_LIBRARY_PATH")]))
    env.update(extra)
    return env


def check(args):
    say(f"platform: {platform_name()} ({platform.platform()}), driver python {platform.python_version()}")
    problems = []
    if not shutil.which("git"):
        problems.append("git not found: install it (sudo apt install git)")
    root, bin_dir, lib = None, None, None
    try:
        root, bin_dir, lib = find_freecad(args.freecad)
        probe = sh([os.path.join(bin_dir, "python"), "-c",
                    "import sys;v=sys.version_info[:2];import FreeCAD;print(v, FreeCAD.Version()[:3])"],
                   run_env(bin_dir, lib, ""))
        say(f"FreeCAD {root}: {probe.stdout.strip() or probe.stderr.strip()[-300:]}")
        if probe.returncode or "(3, 11)" not in probe.stdout:
            problems.append("FreeCAD's python must be 3.11 and import FreeCAD (see output above); "
                            "use the FreeCAD 1.1 AppImage")
    except SystemExit as exc:
        problems.append(str(exc))
    claude = os.environ.get("CLAUDE_CMD") or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    version = sh([claude, "--version"]) if os.path.exists(claude) else None
    if not version or version.returncode:
        problems.append("claude CLI not found or broken: install it (https://claude.ai/install.sh) or set CLAUDE_CMD")
    else:
        status = sh([claude, "auth", "status"])
        try:
            logged_in = json.loads(status.stdout)["loggedIn"]
        except (ValueError, KeyError):
            logged_in = None
        say(f"claude {version.stdout.strip()}; logged in: {'unknown (skipped)' if logged_in is None else logged_in}")
        if logged_in is False:
            problems.append("claude is not logged in: run `claude auth login`")
    if problems and not args.dry_run:
        die("\n  " + "\n  ".join(problems))
    for problem in problems:
        say(f"WARNING (dry-run): {problem}")
    return bin_dir, lib, claude


def setup_deps(bin_dir, lib):
    python = os.path.join(bin_dir, "python")
    deps = os.path.join(HERE, ".deps")
    env = run_env(bin_dir, lib, "")
    missing = [pin for mod, pin in PINS.items()
               if sh([python, "-c", f"import {mod}"], env).returncode]
    if missing:
        say(f"installing into cgb/.deps: {' '.join(missing)}")
        done = subprocess.run([python, "-m", "pip", "install", "--target", deps, "--upgrade", *missing], env=env)
        if done.returncode:
            die("pip install failed; if FreeCAD's python has no pip run `<freecad>/usr/bin/python -m ensurepip` first")


def fixtures():
    data = json.load(open(os.path.join(REPO, "manual/cadgenbench/generation_fixtures.json")))
    return {"gen": list(data["fixtures"]), "edit": list(data["editing_fixtures"])}


def download_data(data_dir, ids):
    api = f"https://huggingface.co/api/datasets/{HF}/tree/main?recursive=true&limit=1000"
    tree = json.load(urllib.request.urlopen(api))
    wanted = [e for e in tree if e["type"] == "file" and e["path"].split("/")[0] in ids]
    for entry in wanted:
        dest = os.path.join(data_dir, entry["path"])
        if os.path.exists(dest) and os.path.getsize(dest) == entry["size"]:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        urllib.request.urlretrieve(f"https://huggingface.co/datasets/{HF}/resolve/main/{entry['path']}", dest)
    say(f"data: {len(wanted)} files under {data_dir}")


def p17_worktree():
    path = os.path.join(REPO, ".arms", "p17")
    if not os.path.isdir(path):
        sh(["git", "fetch", "origin", "repro/p17"], cwd=REPO)
        ref = "origin/repro/p17" if sh(["git", "rev-parse", "--verify", "origin/repro/p17"], cwd=REPO).returncode == 0 \
            else "repro/p17"
        made = sh(["git", "worktree", "add", "--detach", path, ref], cwd=REPO)
        if made.returncode:
            die(f"cannot create the P17 worktree from {ref}: {made.stderr.strip()}")
    return path


def out_base(arm, effort, rep):
    return f"logs/cgb-{arm}-{effort}-r{rep}"


def out_dirs(worktree, arm, effort, rep):
    return sorted(glob.glob(os.path.join(worktree, out_base(arm, effort, rep) + "*/")))


def run_state(worktree, arm, effort, rep, task):
    status = None
    for directory in out_dirs(worktree, arm, effort, rep):
        path = os.path.join(directory, "state.json")
        runs = json.load(open(path))["runs"] if os.path.exists(path) else {}
        for key, run in runs.items():
            if key.split("/")[0] == task and (status is None or run["status"] == "done"):
                status = run["status"]
    return status


def lane_command(python, args, arm, task, phase, data, out):
    flag = ["--task", f"{task}={data}/{task}/input.png"] if phase == "gen" else ["--edit-task", f"{task}={data}/{task}"]
    return [python, "src/tools/lane.py", "--arm", ARMS[arm][1], "--model", args.model, "--effort", args.effort,
            "--retries", "1", "--resume", "--intent", INTENT, "--out", out, *flag]


def reset_seconds(text, default_minutes):
    found = re.findall(r"resets at (\d+):(\d+)", text)
    if not found:
        return default_minutes * 60
    now = datetime.datetime.now()
    target = now.replace(hour=int(found[-1][0]), minute=int(found[-1][1]), second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds() + 300


class Runner:
    def __init__(self, args, jobs, python, worktrees, envs, data):
        self.args, self.jobs, self.python, self.worktrees, self.envs, self.data = args, list(jobs), python, worktrees, envs, data
        self.total, self.finished, self.pause_until, self.stalls = len(jobs), 0, 0.0, {}
        self.lock, self.stop, self.children = threading.Lock(), False, set()

    def take(self):
        with self.lock:
            return self.jobs.pop(0) if self.jobs and not self.stop else None

    def work(self, worker):
        args = self.args
        while job := self.take():
            arm, rep, task, phase = job
            while time.time() < self.pause_until and not self.stop:
                time.sleep(5)
            wt = self.worktrees[arm]
            out = out_base(arm, args.effort, rep) + (f"-w{worker + 1}" if worker else "")
            os.makedirs(os.path.join(wt, out), exist_ok=True)
            logfile = os.path.join(wt, out + ".out")
            with open(logfile, "a+b") as log:
                start = log.tell()
                proc = subprocess.Popen(lane_command(self.python, args, arm, task, phase, self.data, out), cwd=wt,
                                        env=self.envs[arm], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                self.children.add(proc)
                proc.wait()
                self.children.discard(proc)
                log.seek(start)
                text = log.read().decode(errors="ignore")
            if self.stop:
                return
            status = run_state(wt, arm, args.effort, rep, task) or "?"
            stalled = "did not answer in time" in text and self.stalls.get(job, 0) < 3
            if status == "quota" or (status == "failed" and ("refused the request" in text or "ProviderRefused" in text)
                                     and ("resets at" in text or stalled)):
                self.stalls[job] = self.stalls.get(job, 0) + 1
                if status == "failed":
                    self.forget(out, task, wt)
                wait = reset_seconds(text, args.quota_wait)
                self.pause_until = max(self.pause_until, time.time() + wait)
                say(f"quota/provider stall on {arm} r{rep} {task}; all workers wait {wait / 60:.0f} min")
                with self.lock:
                    self.jobs.insert(0, job)
                continue
            with self.lock:
                self.finished += 1
                say(f"{arm} r{rep} {task} {status} [{self.finished}/{self.total}]")

    @staticmethod
    def forget(out, task, wt):
        path = os.path.join(wt, out, "state.json")
        state = json.load(open(path))
        state["runs"] = {k: v for k, v in state["runs"].items() if k.split("/")[0] != task}
        json.dump(state, open(path, "w"), indent=2, sort_keys=True)

    def go(self):
        threads = [threading.Thread(target=self.work, args=(k,), daemon=True) for k in range(self.args.parallel)]
        for thread in threads:
            thread.start()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop = True
            say("interrupted: stopping lanes; rerun the same command to resume")
            for proc in list(self.children):
                os.killpg(proc.pid, signal.SIGTERM)


def table_and_pack(args, worktrees, tasks):
    stamp = f"{socket.gethostname()}-{datetime.date.today():%Y%m%d}"
    results = os.path.join(HERE, "results")
    os.makedirs(results, exist_ok=True)
    archive = os.path.join(results, f"daedalus-cgb-{stamp}.zip")
    print(f"{'arm':10} {'rep':>3} {'phase':5} {'done':>5} {'failed':>6} {'missing':>7}")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for arm in args.arms:
            wt = worktrees[arm]
            for rep in range(1, args.reps + 1):
                for phase, ids in tasks.items():
                    states = [run_state(wt, arm, args.effort, rep, t) for t in ids]
                    print(f"{arm:10} {rep:>3} {phase:5} {states.count('done'):>5} "
                          f"{sum(s in ('failed', 'quota') for s in states):>6} {states.count(None):>7}")
                for directory in out_dirs(wt, arm, args.effort, rep):
                    for name in glob.glob(directory + "**", recursive=True):
                        if os.path.isfile(name) and name.endswith((".json", ".jsonl", ".step")):
                            bundle.write(name, os.path.join(arm, os.path.relpath(name, os.path.join(wt, "logs"))))
    say(f"zip: {archive} ({os.path.getsize(archive) / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", default=list(ARMS), choices=list(ARMS))
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--effort", default="medium", choices=("low", "medium", "high", "xhigh"))
    parser.add_argument("--model", default="opus")
    parser.add_argument("--phases", default="gen,edit")
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--tasks", help="comma-separated task ids, e.g. 101,201")
    parser.add_argument("--smoke", action="store_true", help="1 gen + 1 edit task, 1 rep")
    parser.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    parser.add_argument("--pack", action="store_true", help="only build the results zip")
    parser.add_argument("--quota-wait", type=int, default=30, help="minutes to wait when no reset time is printed")
    parser.add_argument("--freecad", help="FreeCAD directory (holds usr/bin/freecadcmd)")
    parser.add_argument("--data", help="existing task data directory (skips the download)")
    args = parser.parse_args()
    fx = fixtures()
    tasks = {p: fx[p] for p in args.phases.split(",")}
    if args.smoke:
        args.reps, tasks = 1, {p: ids[:1] for p, ids in tasks.items()}
    if args.tasks:
        picked = set(args.tasks.split(","))
        tasks = {p: [t for t in ids if t in picked] for p, ids in tasks.items()}
    worktrees = {a: os.path.join(REPO, ".arms", ARMS[a][0]) if ARMS[a][0] else REPO for a in args.arms}
    if args.pack:
        return table_and_pack(args, worktrees, tasks)
    bin_dir, lib, claude = check(args)
    data = args.data or os.path.join(HERE, "data")
    if not args.dry_run:
        setup_deps(bin_dir, lib)
        if not args.data:
            download_data(data, {t for ids in tasks.values() for t in ids})
        for arm in args.arms:
            if ARMS[arm][0]:
                worktrees[arm] = p17_worktree()
    python = os.path.join(bin_dir or "<freecad>/usr/bin", "python")
    envs = {a: run_env(bin_dir or "", lib or "", claude, ARMS[a][2]) for a in args.arms}
    jobs = [(a, r, t, p) for r in range(1, args.reps + 1) for a in args.arms for p, ids in tasks.items() for t in ids
            if run_state(worktrees[a], a, args.effort, r, t) != "done"]
    total = args.reps * len(args.arms) * sum(map(len, tasks.values()))
    say(f"{len(jobs)} jobs left of {total}; {args.parallel} parallel")
    if args.dry_run:
        say(f"env: {' '.join(f'{k}={shlex.quote(envs[args.arms[0]][k])}' for k in ('FREECAD_CMD', 'FREECAD_LIB', 'CLAUDE_CMD', 'PYTHONPATH', 'QT_QPA_PLATFORM'))}")
        for arm, rep, task, phase in jobs:
            extra = " ".join(f"{k}={v}" for k, v in ARMS[arm][2].items())
            out = out_base(arm, args.effort, rep)
            print(f"cd {shlex.quote(worktrees[arm])} && {extra} " + shlex.join(
                lane_command(python, args, arm, task, phase, data, out)))
        return
    Runner(args, jobs, python, worktrees, envs, data).go()
    table_and_pack(args, worktrees, tasks)


if __name__ == "__main__":
    main()
