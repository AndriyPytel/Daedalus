#!/bin/bash
set -euo pipefail

LINK="/Users/apytel/Library/Application Support/FreeCAD/v1-1/Mod/FreeCadCopilot"
TARGET="/Users/apytel/Projects/FreeCadCopilot-wt/rlm-version"

current() {
  if [ -L "$LINK" ]; then
    readlink "$LINK"
  else
    echo "(not a symlink)"
  fi
}

running() {
  pgrep -f "FreeCAD.app/Contents/MacOS/FreeCAD" >/dev/null \
    || pgrep -f "FreeCAD.app/Contents/Resources/bin/freecad" >/dev/null
}

case "${1:-status}" in
  status)
    echo "$LINK"
    echo "-> $(current)"
    echo "seams are set per launch: RLM_AGENTIC=on|off (unset = today's shipped mix), not by this script"
    ;;
  switch)
    if running; then
      echo "FreeCAD is running. Quit it first, then re-run this script." >&2
      exit 1
    fi
    if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
      echo "$LINK exists and is not a symlink. Refusing to touch it." >&2
      exit 1
    fi
    rm -f -- "$LINK"
    ln -s -- "$TARGET" "$LINK"
    echo "$LINK -> $TARGET"
    echo "seams are set per launch: RLM_AGENTIC=on|off (unset = today's shipped mix), not by this script"
    ;;
  *)
    echo "usage: $0 [status|switch]" >&2
    exit 1
    ;;
esac
