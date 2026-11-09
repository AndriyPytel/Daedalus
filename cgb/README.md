# CADGenBench re-run kit

Re-runs four Daedalus arms (p17, p17-whole, a4h, a4h-whole) on the CADGenBench tasks, 3 reps each, and packs the logs into one zip.

## Prerequisites (Windows: WSL2 Ubuntu; or plain Linux)

Everything below runs inside the Ubuntu terminal, not in Windows.

1. WSL2: in PowerShell as admin `wsl --install -d Ubuntu`, reboot, open "Ubuntu".
2. `sudo apt update && sudo apt install -y git python3 curl`
3. GitHub access to the private repo: accept the collaborator invite, then `sudo apt install -y gh && gh auth login`.
4. FreeCAD 1.1 Linux AppImage (py311), extracted to `~/freecad`:
   ```
   curl -fLO https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage
   chmod +x FreeCAD_*.AppImage && ./FreeCAD_*.AppImage --appimage-extract >/dev/null && mv squashfs-root ~/freecad
   ```
5. The `claude` CLI inside WSL (a Windows install does not count): `curl -fsSL https://claude.ai/install.sh | bash`, then `claude auth login` with the Claude Max account. The run spends that subscription's quota.

The full run takes days. Keep the computer awake (Windows power settings: never sleep) and the Ubuntu window open.

## Run

```
gh repo clone AndriyPytel/Daedalus -- -b cgb-repro && cd Daedalus
python3 cgb/run_cgb.py --smoke     # 1 gen + 1 edit task per arm: check the setup first
python3 cgb/run_cgb.py             # the full run
```

The script checks the tools, installs its Python packages into `cgb/.deps`, downloads the task data into `cgb/data`, and creates the P17 worktree `.arms/p17`. It installs nothing system-wide.

- Safe to stop with Ctrl+C. Run the same command again to resume.
- When the subscription limit is hit, it waits for the reset and continues.
- Useful options: `--arms a4h a4h-whole`, `--reps 1`, `--parallel 2`, `--phases gen`, `--tasks 101,201`, `--freecad DIR`, `--dry-run`.

## Send back

The last step writes `cgb/results/daedalus-cgb-<host>-<date>.zip` and a done/failed/missing table. `python3 cgb/run_cgb.py --pack` rebuilds it. Send the zip.
