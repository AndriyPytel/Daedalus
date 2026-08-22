# Diagnosis: Stop button — reasoning continues, FreeCAD crashes

Read-only. No source file changed. Working tree has uncommitted changes in
`ui/` and `core/`; see git-diff note at the end — none touch the stop path.

Evidence: `logs/sessions/cd594cb0281244f7b366d94dd8740714.jsonl` (85 events,
`kind`/`at`). `~/Library/Application Support/FreeCAD/v1-1/Mod/FreeCadCopilot`
is a symlink to this repo, so the user ran this exact working tree.

## a) What happens when Stop is pressed

1. `CopilotPanel.on_stop` (`ui/copilot_panel.py:1568-1583`) disables the
   question card and calls `self.worker.stop()`, then returns — it never
   waits for the run.
2. `LoopWorker.stop` (`:522-524`): `self._stop.set()` (`threading.Event`)
   plus `self.ask.abandon()`, releasing a waiting question with a synthetic
   `Approval(REJECT)` (`:483-489`). A run parked on a question stops at once.
3. A run inside `core.loop.run` is only cooperative: `cancelled` is
   `self._stop.is_set` (`:598`), checked at ~10 sites between phases/Steps
   (`core/loop.py:1547,1897,1950,2204,2467,2536,2696,2798,3126,3138,3206`).
   None sit inside a provider call.
4. The log confirms it: `call_started` phase=execute at t=650.3s
   (≈22:25:02, `ceiling=300` → `ClaudeCodeClient`, `core/llm.py:534-538`);
   `cost` for the *same* call at t=739.6s (≈22:26:27, `seconds=89.3`, fully
   billed); `blocked` at the same instant, "Stopped by you…". Stop landed
   somewhere in that 89s window; the call ran to completion. The
   `cancelled()` that fired is `core/loop.py:3138`, entering `_work_through`'s
   Step loop right after `execute()` returned (`:3131`); caught in `run()`
   (`:1806-1812`), turned into `BLOCKED`.
5. `finished` fires (`ui/copilot_panel.py:596`) → `on_run_finished`
   (`:2275-2304`) resets UI state. The worker's `QThread` is **not** stopped
   here — it stays alive in its Qt event loop for the Session's life
   (`:668-677`).

## b) Why reasoning keeps appearing after Stop — confirmed

`_streamed` (`core/loop.py:425-461`, "the one place every phase's stream is
consumed") does `for chunk in llm.stream(...): events.emit(THOUGHT, ...)`
with **no `cancelled()` in the loop**, and `cancelled` is not even a
parameter. Neither client can be cancelled mid-stream:

- `LiteLLMClient.stream` (`core/llm.py:117-178`): plain `for raw in
  completion(**kwargs)` over LiteLLM's HTTP generator, no cancel hook.
- `ClaudeCodeClient._answered` (`:570-631`): blocking `for line in
  process.stdout` (`:602`); only `threading.Timer(self.timeout, give_up)`
  (`:594-595`, up to 300s) ends it early — `self._stop` never reaches it.

So `AccordionWidget` keeps rendering `THOUGHT` events
(`ui/copilot_panel.py:1826-1848`) for as long as the provider streams, Stop
or not. Stop only changes what happens *after* the stream ends — one
`cancelled()` check later than the user expects, which for a multi-minute
Execute call reads as "Stop does nothing."

## c) Most probable crash mechanism, ranked

The addon log has no events after `blocked` (22:26:27); FreeCAD relaunched
(pid 30779) at 22:26:57, 30s later, no `.ips` in `~/Library/Diagnostic
Reports`. An empty report folder fits a **hang the user force-quit** better
than a hard segfault, which macOS normally reports — favours #1 below.
Labelled where it goes beyond what the code proves.

**1. `QThread.terminate()` on a worker still busy at teardown — highest confidence.**
`_cleanup_thread` (`ui/copilot_panel.py:2328-2358`) runs on
`app.aboutToQuit` (`:767`) *and* `closeEvent` (`:2403-2406`) — exactly what a
user does after Stop "does nothing": close the panel or quit FreeCAD. For
every Session it calls `bundle.thread.wait(SHUTDOWN_GRACE_MS)` (3000ms,
`:2356`) **on the main thread**, then `bundle.thread.terminate()` on timeout
(`:2357`; `shut_down`, `:679-688`, same for `delete_session`/
`slotDeletedDocument`) — an unsafe, Qt-documented-dangerous kill. Two things
make 3s easy to miss even though *this* run had ended:
  - `on_stop()` only stops the *current* Session's worker (`:776-778`), not
    `self._workers.values()` — another open Session can still be mid-call.
  - `_Dispatcher.on_main_thread` (`core/apply.py:52-107`) blocks the worker
    on `done.wait(timeout)` (`:102`) for a **queued** signal only the main
    thread's event loop delivers (`self._relay.job.emit(run)`, `:101`).
    `QThread::wait()` does not pump events, so `_cleanup_thread` waiting on
    that worker deadlocks: worker waits for main thread, main thread waits
    for worker. Hits the 3s ceiling every time; `terminate()` then kills the
    thread possibly mid-C-call inside CPython/Qt/FreeCAD bindings — the known
    way forced termination corrupts interpreter state (crash, or a hang
    matching the missing `.ips`).
  - *Speculative*: the queued Relay job (`core/apply.py:73-83`) captures the
    real `func` — for an apply, `run_in_transaction(doc, code, ...)`
    (`:135-150`) against the *live* Document. If the worker is killed before
    delivery, the job can still run afterward against a Document whose
    Session is already gone — matches "worker thread not joined before the
    state it writes into is dropped."

**2. Two threads on one `subprocess.Popen`, no lock — lower confidence.**
`PreflightWorker._ask` (`core/preflight.py:243-267`) writes
`self._process.stdin` from the worker thread; `PreflightWorker.kill`
(`:168-176`, via `ApplyPipeline.shutdown`, `core/apply.py:223-230`) calls
`self._process.kill()` from the main thread, no lock. Real race, but the
preflight worker runs a **throwaway copy** of the Document in a **separate
process** (`core/preflight.py:5-8`, `load_fresh`,
`core/preflight_worker.py:588`) — worst case a caught exception
(`core/preflight.py:257-259`), not the parent process going down.

**3. Stale `document` reaching `run_in_transaction` — speculative, no log evidence.**
If the user closed a Document (`slotDeletedDocument`, `:2360-2374`) while an
apply for it was in flight, `run_in_transaction` (`core/apply.py:135-150`)
would mutate a C++ object FreeCAD is simultaneously destroying — a classic
segfault. The log shows no apply in flight at `blocked` (the last
`preflight_failed` at t=650.3s had already returned), so this needs a later,
unlogged apply.

## d) Smallest fix per mechanism, and its test

- **(b)**: thread `cancelled` into `_streamed` (`core/loop.py:425`) and
  `llm.stream()` (`core/llm.py:117`, `542`); break the `for chunk in ...`
  loop on it (LiteLLM: stop iterating, let the generator close the HTTP
  stream; Claude CLI: `process.kill()` inside `_answered`'s loop, `:602-616`,
  not only from the ceiling timer). Test: extend `core/loop.py`'s
  cancellation tests with `cancelled()`-true mid-stream, plus a `core/llm.py`
  case asserting a cancelled stream stops consuming/billing.
- **(c-1)**: stop every Session's worker before waiting on any (already
  done, `:2349-2350`) **and** make `_Dispatcher.on_main_thread` cancellable,
  or have `_cleanup_thread`/`shut_down` pump
  `QCoreApplication.processEvents()` while waiting instead of a blind
  `thread.wait()`, breaking the deadlock. Test: extend
  `tests/test_main_thread_dispatch.py` with a dispatch in flight during
  `_cleanup_thread`/`shut_down`, asserting `thread.wait()` succeeds without
  `terminate()`.
- **(c-3)**: have `run_in_transaction` (`core/apply.py:135-150`) check the
  Document is still live before touching it, and have `slotDeletedDocument`
  (`:2360-2374`) cancel the bundle before `shut_down` so a queued dispatch
  for that Document is skipped. Test: new `tests/test_panel_level2.py` case
  closing a Document mid-apply, asserting no transaction runs against it.

## git diff note

`git diff --stat` touches `ui/copilot_panel.py`, `ui/chat_widgets.py`,
`ui/accordion_widget.py`, `core/loop.py`, `core/freecad_tools.py`,
`core/lint.py`, `core/preflight_worker.py`, `config/prompts/execute.md`, and
tests. None of the hunks touch `on_stop`, `_stop`, `cancelled`, `_streamed`,
`llm.stream`, `_Dispatcher`, `_cleanup_thread`, or `shut_down` — the changes
are a `DocumentLinkWidget`→`FileChip` render swap, a label-sizing fix, an
accordion header layout fix, and one wording change in Execute's failure
hints. The stop path on disk is the code that produced this session.
