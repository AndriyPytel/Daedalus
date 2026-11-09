# CADGenBench re-run kit

Re-runs four Daedalus arms (p17, p17-whole, a4h, a4h-whole) on the CADGenBench tasks, 3 reps each, and packs the logs into one zip.

## Prerequisites (WSL2 Ubuntu or Linux)

1. `git`, `python3` (>= 3.10).
2. FreeCAD 1.1 Linux AppImage, extracted:
   ```
   chmod +x FreeCAD*.AppImage && ./FreeCAD*.AppImage --appimage-extract && mv squashfs-root ~/freecad
   ```
3. The `claude` CLI, then `claude auth login` (a Claude subscription; the run spends its quota).

## Run

```
git clone -b cgb-repro https://github.com/AndriyPytel/Daedalus.git && cd Daedalus
python3 cgb/run_cgb.py --smoke     # 1 gen + 1 edit task per arm: check the setup first
python3 cgb/run_cgb.py             # the full run
```

The script checks the tools, installs its Python packages into `cgb/.deps`, downloads the task data into `cgb/data`, and creates the P17 worktree `.arms/p17`. It installs nothing system-wide.

- Safe to stop with Ctrl+C. Run the same command again to resume.
- When the subscription limit is hit, it waits for the reset and continues.
- Useful options: `--arms a4h a4h-whole`, `--reps 1`, `--parallel 2`, `--phases gen`, `--tasks 101,201`, `--freecad DIR`, `--dry-run`.

## Send back

The last step writes `cgb/results/daedalus-cgb-<host>-<date>.zip` and a done/failed/missing table. `python3 cgb/run_cgb.py --pack` rebuilds it. Send the zip.
