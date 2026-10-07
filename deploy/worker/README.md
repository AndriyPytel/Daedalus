# Worker image

Headless FreeCAD 1.1.4 and `sdk.Client` on Linux x86_64, for a Session on the home server.

Build from the repository root (the context needs `src`, `config` and `deploy`):

    podman build -t dae-496-worker -f deploy/worker/Containerfile .
    podman run --rm dae-496-worker python /app/deploy/worker/smoke.py /tmp/smoke.step

## What is in it

- `debian:trixie-slim` with no GUI, Qt, X11 or OpenGL package installed.
- The official `FreeCAD_1.1.4-Linux-x86_64-py311.AppImage`, pinned by sha256 and extracted at build time, so nothing
  needs FUSE at run time. `freecadcmd` and the bundled Python 3.11 both import `FreeCAD` with no display.
- `httpx`, the one package `sdk` imports that the AppImage Python lacks. Everything else `sdk` imports is bundled.
- `FREECAD_CMD` points the Client's kernel at the extracted `freecadcmd`.

## FreeCADGui: disabled, not served by Xvfb

`FreeCADGui` imports with no display, but it is an empty shell: no `getMainWindow`, no `ActiveDocument`, no view.
Every reader in `core.freecad_apply` already guards its `import FreeCADGui` with `except (ImportError, AttributeError)`:

| Path | Without a display |
| --- | --- |
| `_selection` | `[]` |
| `_active_view`, so `capture_review_views` (the five review pictures) | `None`, `[]` |
| `_active_body` | falls back to the only Body |
| `lint` | reads source text only, never imports the GUI |

`ApplyPipeline.main_window`, `start_run` and `end_run` reach `Gui.getMainWindow()`; `sdk.Client` calls none of them.

Decision: the worker runs with the GUI paths disabled and ships no Xvfb. A worker therefore cannot produce review
pictures; a future need for them is a new ticket with its own image layer. `smoke.py` records the answers above as JSON
and `tests/test_worker_image.py` asserts them.

## Smoke test

`smoke.py` drives one `sdk.Client` with a scripted LLM on the real headless kernel: a 10 mm cube, exported as STEP.
The test copies the STEP to the Mac and reads it back with `cadgenbench.common.validity.analyze_step`.
