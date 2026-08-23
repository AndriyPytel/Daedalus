#!/bin/bash
set -euo pipefail

LINK="/Users/apytel/Library/Application Support/FreeCAD/v1-1/Mod/FreeCadCopilot"
OFF_TARGET="/Users/apytel/Projects/FreeCadCopilot"
ON_TARGET="/Users/apytel/Projects/FreeCadCopilot-wt/rlm-version"

current() {
  if [ -L "$LINK" ]; then
    readlink "$LINK"
  else
    echo "(not a symlink)"
  fi
}

label_for() {
  case "$1" in
    "$OFF_TARGET") echo "dev (main checkout)" ;;
    "$ON_TARGET") echo "rlm-version (worktree)" ;;
    *) echo "unknown ($1)" ;;
  esac
}

running() {
  pgrep -f "FreeCAD.app/Contents/MacOS/FreeCAD" >/dev/null \
    || pgrep -f "FreeCAD.app/Contents/Resources/bin/freecad" >/dev/null
}

case "${1:-status}" in
  status)
    echo "$LINK"
    echo "-> $(current)"
    echo "$(label_for "$(current)")"
    echo "seams are set per launch: RLM_AGENTIC=on|off (unset = today's shipped mix)"
    ;;
  use-rlm|use-dev)
    if running; then
      echo "FreeCAD is running. Quit it first, then re-run this script." >&2
      exit 1
    fi
    target=$([ "$1" = use-rlm ] && echo "$ON_TARGET" || echo "$OFF_TARGET")
    if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
      echo "$LINK exists and is not a symlink. Refusing to touch it." >&2
      exit 1
    fi
    rm -f -- "$LINK"
    ln -s -- "$target" "$LINK"
    echo "$LINK -> $target"
    echo "this moves the CHECKOUT only; the seams are moved by RLM_AGENTIC=on|off"
    ;;
  *)
    echo "usage: $0 [status|use-rlm|use-dev]" >&2
    exit 1
    ;;
esac
