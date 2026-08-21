# UI inventory

What `ui/` does today, measured. 2606 lines over nine modules, plus the two
files that reach it: `InitGui.py` and `core/events.py`.

## Method

Every line of every file under `ui/` is assigned to exactly one of four
buckets, by line range, so the buckets sum to the file. A range takes the
character of the code in it, not of the module it sits in.

| Bucket | Meaning |
| --- | --- |
| Rendering | Widget construction, layout, stylesheet, text placement. What is on screen and nothing else. |
| Logic | Code in `ui/` that is not rendering and is not Qt: rules, state, lifecycle, orchestration. |
| Coupling | Qt or FreeCAD by nature: threads, blocking dialogs, clipboard, file dialogs, palette reads, docking, the binding shim. |
| Delegation | A method in `ui/` whose whole body is a call into `core.sessions`. |

The Qt binding shim (`try: from PySide2 … except ImportError: from PySide …`,
five lines in each of the nine files) counts as coupling. Module docstrings and addon
imports count as rendering.

## 1. Component inventory

| Component | Where | Lines | What it does | Character |
| --- | --- | --- | --- | --- |
| Dock frame | `ui/copilot_panel.py:405-424` | 20 | `QDockWidget` named `FreeCadCopilotPanel`, root layout, header, chat stack, status line, input card | Rendering |
| Panel stylesheet | `ui/copilot_panel.py:41-135` | 95 | Geometry and typography only, no colour except `rgba(127,127,127,40)` washes | Rendering |
| Header bar | `ui/copilot_panel.py:476-511` | 36 | 44px bar: session title, New chat, History | Rendering |
| Session title | `ui/copilot_panel.py:485-491`, `855-859` | 12 | Shows the active Session's name; double-click calls `rename_current_session` | Rendering |
| Composer | `ui/copilot_panel.py:139-160`, `524-534`, `578-609` | 64 | Enter sends, Shift+Enter newlines, Ctrl+V with an image goes to the upload widget, height grows to 40% of the panel then scrolls | Rendering, except `149-155` (clipboard probe) |
| Model combo | `ui/copilot_panel.py:550-559`, `625-701` | 85 | Rows grouped under provider headings that carry `Qt.NoItemFlags`; selects the stored model; retires a model no provider offers; drops the client on change | 10 rendering, 75 logic |
| Send button | `ui/copilot_panel.py:562-566`, `978-982` | 10 | Circular button, hidden while a run is in flight | Rendering |
| Stop button | `ui/copilot_panel.py:568-573`, `964-976` | 19 | Shown in the send button's place mid-run; disables an open question card and stops the worker | 6 rendering, 13 logic |
| Upload button | `ui/upload_widget.py:148-195` | 48 | `+` opens `QFileDialog.getOpenFileNames` filtered to images and PDF | 34 rendering, 14 coupling |
| File chips | `ui/upload_widget.py:20-145` | 126 | One chip per attached file, icon by extension, name truncated at 22, `×` on hover; the bar hides itself when empty | 120 rendering, 6 logic |
| Clipboard attachment | `ui/upload_widget.py:197-238` | 42 | Writes a clipboard image to a temp PNG, attaches it, and deletes only the files it wrote | Coupling and logic |
| Status line | `ui/copilot_panel.py:431-437`, `1060-1062` | 10 | One replaceable line outside the transcript's scroll area; `SizePolicy.Ignored` so a long Step cannot widen the dock | Rendering |
| Empty state | `ui/copilot_panel.py:844-853` | 10 | One centred line naming the current model and the Plan Gate | Rendering |
| Session stack | `ui/multi_tab_manager.py:24-45`, `133-149`, `208-212`, `226-229` | 44 | `QStackedWidget`, one chat area per Session, at the Session's own position | Rendering |
| Transcript column | `ui/multi_tab_manager.py:47-131` | 85 | Scroll area ending in a stretch; follows the bottom only when the user was already there; `QScroller` gesture; 250ms scroll animation held on the scroll area | Rendering |
| History popup | `ui/history_popup.py:107-205` | 99 | Frameless `Qt.Popup`, fixed 400px high, search field filtering by title | 90 rendering, 9 coupling (`198-205`) |
| History row | `ui/history_popup.py:9-105` | 97 | Name, relative time, delete button; the time hides when the row is too narrow; tooltip is the Document | 88 rendering, 9 coupling (`75-83`) |
| User bubble | `ui/chat_widgets.py:11-39` | 29 | `QLabel` in a translucent grey bubble, selectable, word-wrapped | Rendering |
| Assistant message | `ui/chat_widgets.py:42-67` | 26 | Same without the bubble, plus `append_text` | Rendering |
| Document link | `ui/chat_widgets.py:70-87` | 18 | An assistant line whose text is `<a href="file://…">`, `setOpenExternalLinks(True)` | Coupling |
| Code block | `ui/chat_widgets.py:90-121` | 32 | Read-only `QTextEdit`, monospace font read from FreeCAD's editor preference | Rendering |
| Accordion | `ui/accordion_widget.py:18-89` | 72 | Toggle row plus a plain-text `QLabel` beneath it; `append_text` concatenates; opacity pulse while a phase is live | Rendering |
| Plan widget | `ui/plan_widget.py:20-91` | 72 | One row per Step, marker `○ ▸ ✓ ✗`, `highlight(n)` 1-based, colour only for running (`palette(highlight)`) and failed (`#f44336`) | Rendering |
| Attachment picture | `ui/copilot_panel.py:1075-1097` | 23 | `QPixmap` of the path the event carries, scaled to 320px; a file that no longer decodes falls back to `📎 name` | Rendering |
| Question card | `ui/copilot_panel.py:1258-1309` | 52 | One `QFrame#GateCard`: prompt, note, a pill button per choice; a choice is a label and a callable returning the answer | Rendering |
| Plan Gate prompt | `ui/copilot_panel.py:1165-1187` | 23 | Four choices: Approve, Approve and carry on (`standing=True`), Amend (answer from the composer), Reject | Rendering |
| Failed-result prompt | `ui/copilot_panel.py:1189-1211` | 23 | Three choices: `ACCEPT`, `REVISE`, `START_OVER`, over a line naming how many Steps were applied | Rendering |
| Missing-values prompt | `ui/copilot_panel.py:1213-1234` | 22 | Two choices: Send (answer from the composer) and "You decide" (empty answer); Enter is bound to Send | Rendering |
| Owned-object prompt | `ui/copilot_panel.py:1236-1256` | 21 | Two choices, Allow and Refuse, naming the object kinds to be deleted | Rendering |
| Answer return path | `ui/copilot_panel.py:1311-1341` | 31 | Disables the card, sets the status line, hands the value to `AskTheUser.answer`; `_from_composer` echoes what was typed and returns None when nothing was | 18 rendering, 13 coupling |
| Icons and fonts | `ui/ui_helpers.py:1-170` | 170 | 13 inline SVGs, tinted to the widget they sit on; monospace and UI font read from FreeCAD preferences | 102 rendering, 68 coupling |
| Dock install | `ui/copilot_dock.py:1-51` | 51 | Module-level singleton; `main_window.addDockWidget(RightDockWidgetArea, panel)` | Coupling |

## 2. The event contract

`core/events.py:59-63` defines 22 kinds. `Event` is `kind: str`, `data: dict`,
`at: float` (`core/events.py:66-74`). The panel subscribes through
`EventBridge` and switches on the kind in `CopilotPanel.on_event`
(`ui/copilot_panel.py:984-1045`); what an event *says* is `core/narrate.py`,
shared with `tools/run_slice.py`.

| Kind | Emitted | Payload | UI |
| --- | --- | --- | --- |
| `intent` | `core/loop.py:1733` | `text: str` | Line in an `AssistantMessageWidget` (`narrate.py:93-94`) |
| `thought` | `core/loop.py:432` | `phase: str, text: str` | Appends to one accordion per phase, and to the status line truncated to 90 chars (`copilot_panel.py:1008-1010`, `1064-1073`) |
| `question` | `core/loop.py:1843,2617,2746`, `core/apply.py:446` | `text: str` | Line (`narrate.py:96-97`) |
| `answer` | `core/loop.py:1775,2624,2633,2635,2637,2749`, `core/apply.py:448` | `text: str, by: str` | Line, except `by="user"`, which is already in the transcript (`narrate.py:99-105`) |
| `attached` | `core/loop.py:2704` | `files: [{name: str, path: str}]` | Decodes each `path` with `QPixmap` and shows it at 320px (`copilot_panel.py:1075-1097`) |
| `design` | `core/loop.py:1854,2614` | `model: dict` (nested dicts/lists/strings, `core/design.py`) | Nothing. Named in `tests/test_panel_renders_the_stream.py:15` as written-not-shown |
| `part` | `core/loop.py:2323` | `path: str` | `DocumentLinkWidget` linking `file://` at that path (`copilot_panel.py:1099-1114`) |
| `plan` | `core/loop.py:1187` | `steps: [str], acceptance: [str], unanswered: [str], decided: [str]` | `PlanWidget` plus up to three accordions, all opened (`copilot_panel.py:1116-1148`) |
| `plan_revised` | `core/loop.py:1187` | as `plan`, plus `reason: str, revision: int, of: int, because: str` | Line, then the same render under the title "Revised Plan" (`copilot_panel.py:1043-1045`) |
| `gate` | `core/loop.py:2811,2815,2822` | `steps: int, asked: bool, assumed: int` / `decision: str, standing: bool` | Status line only (`narrate.py:114-119`) |
| `step_started` | `core/loop.py:3047` | `index: int, total: int, description: str` | `PlanWidget.highlight(index)`, closes the open thought, status line (`copilot_panel.py:1029-1033`) |
| `tool_call` | `core/loop.py:1205,1313,1490,1516,3068` | `name: str, arguments: dict` | `CodeBlockWidget` when `arguments` has `code`, otherwise an accordion of `str(arguments)` (`copilot_panel.py:1150-1163`) |
| `tool_result` | `core/loop.py:1209,1211,1326,1492,1518,3070` | `name: str, result: dict, already_known: bool` | Status line `name → result["status"]` (`narrate.py:139-141`) |
| `preflight_failed` | `core/loop.py:3092` | `step: int, attempt: int, traceback: str` | Accordion, title and body (`narrate.py:143-145`) |
| `applied` | `core/loop.py:3082` | `step: int, label: str, created: [str]` | Line, and `PlanWidget.mark(step, done)` (`copilot_panel.py:1035-1036`) |
| `assertion` | `core/loop.py:1496,1523,1530,1537` | `ok: bool\|None, code: str, confirmed: [str], uncovered: [str], traceback: str, unmet: [str]` | Line, three outcomes (`narrate.py:183-200`) |
| `outstanding` | `core/loop.py:2702` | `entries: [str]` | Line plus an accordion listing the entries (`narrate.py:121-128`) |
| `unbuilt` | `core/loop.py:3055` | `step: int, total: int, description: str, why: str, entries: [str]` | Line (`narrate.py:155-162`) |
| `review_verdict` | `core/loop.py:2896` | `verdict: str, reason: str` | Line (`narrate.py:164-167`) |
| `blocked` | `core/loop.py:3197` | `reason: str, tried: [{step: int, code: str, traceback: str}], provider_said: str` | Line, an accordion of every attempt, and `PlanWidget.mark(current, failed)` (`copilot_panel.py:1038-1039`) |
| `cost` | `core/loop.py:438` | `phase: str, usd: float, model: str` | Nothing. Written to the session log only |
| `error` | `core/loop.py:1765,1768,2014`, `ui/copilot_panel.py:343,356` | `message: str` | Accordion, title and body (`narrate.py:177-178`) |

### Is the seam JSON-clean

Yes, in practice, and unenforced.

- Every payload above is `str`, `int`, `float`, `bool`, `list` or `dict` of
  those. `acceptance` is `[entry["says"] for entry in entries(the_part)]`, a
  list of strings (`core/loop.py:846-847`); `unmet` is `List[str]`
  (`core/loop.py:349`); `tried` entries are `{"step", "code", "traceback"}`
  (`core/loop.py:3192-3193`); tool results are dicts, and `core/tools.py:1-12`
  states the registry never imports FreeCAD.
- No Qt type crosses the stream. `EventStream.emit` is called from
  `core/loop.py` and `core/apply.py:366` only, neither of which imports Qt for
  event data.
- Nothing enforces it. `Event.as_json` passes `default=str`
  (`core/events.py:72-74`), so a Python object would be silently stringified
  rather than rejected — asserted by
  `tests/test_session_log.py:120-127`, which emits `created=[object()]` and
  checks only that one line was written.
- The stream already round-trips: `replay` reads a log back into `Event`s and
  the panel renders a restored Session by feeding them to the same `on_event`
  (`core/events.py:159-186`, `ui/copilot_panel.py:780-824`).
- Two payloads are strings whose meaning is a path on the local filesystem:
  `attached.files[].path` and `part.path`. Both are read as files by the
  renderer.

### What does not cross the stream

The four questions. They are callables passed into `run` — `gate`, `ask`,
`decide` (`core/loop.py:1708-1710`) and `confirm` on the pipeline
(`ui/copilot_panel.py:302`) — and each blocks the calling thread until a value
is returned. The events named `question` and `gate` are narration of that
exchange, not the exchange.

## 3. Couplings a browser cannot do

| # | Coupling | Where | What it is |
| --- | --- | --- | --- |
| 1 | Blocking question gate | `ui/copilot_panel.py:177-255` | `AskTheUser._ask` emits a Qt signal and then calls `threading.Event.wait()` on the worker thread (`238-243`). The run is suspended inside `core.loop.run` until `answer()` sets it (`245-247`). Four askers use it: `about_the_plan`, `about_objects_we_do_not_own`, `about_missing_values`, `about_the_revision`. `abandon()` releases a waiting question with `Approval(REJECT)` so Stop is not swallowed (`249-255`) |
| 2 | Worker thread | `ui/copilot_panel.py:442-448`, `464` | A `QThread`; `LoopWorker.moveToThread(self.thread)`; `request_work` is a `Signal(str, object, str, object)` connected to `do_work`, which is a `@QtCore.Slot`. `tests/test_panel_renders_the_stream.py:105-117` asserts `moveToThread` exists |
| 3 | Event thread hop | `ui/copilot_panel.py:163-174` | `EventBridge.event` is `Signal(object)`; `subscriber()` hands `signal.emit` to `EventStream`, and Qt's queued connection moves each event from the worker thread to the GUI thread |
| 4 | Main-thread Document dispatch | `core/apply.py:52-117` | Every Document read or write is marshalled onto the Qt main thread by a `QObject` relay living in `app.thread()`, and the worker blocks on `threading.Event` until it returns. `tests/test_main_thread_dispatch.py:70-111` is the test |
| 5 | Fitting the 3D view | `core/apply.py:303`, `core/freecad_tools.py:147-190` | Every landed Step calls `fit_view`, which reads `FreeCADGui.ActiveDocument.ActiveView`, falls back to `mdiViewsOfType("Gui::View3DInventor")` and calls `fitAll()` |
| 6 | Docking into FreeCAD | `ui/copilot_dock.py:23-40`, `InitGui.py:42-45` | `main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)`, retried on a `QTimer.singleShot(0, …)` when the window does not exist yet. The dock names itself so `QMainWindow::saveState()` keeps its position (`ui/copilot_panel.py:407`) |
| 7 | Native file dialog | `ui/upload_widget.py:183-195` | `QFileDialog.getOpenFileNames` returns absolute local paths, which are what the run is handed |
| 8 | Clipboard image | `ui/upload_widget.py:197-223`, `ui/copilot_panel.py:149-155` | `QApplication.clipboard().image()` → `QPixmap.fromImage` → `tempfile.NamedTemporaryFile(suffix=".png", delete=False)` → the path is attached. The composer intercepts Ctrl+V before the text field sees it |
| 9 | Opening a file from the transcript | `ui/chat_widgets.py:70-87` | `QUrl.fromLocalFile(path)` with `setOpenExternalLinks(True)`: clicking the part line hands the path to the OS |
| 10 | Rendering an attachment from disk | `ui/copilot_panel.py:1086-1097` | `QPixmap(attached["path"])` decodes a local file; a path that no longer resolves degrades to the filename |
| 11 | Modal rename | `ui/copilot_panel.py:754-763` | `QInputDialog.getText`, blocking |
| 12 | Modal delete confirmation | `ui/copilot_panel.py:830-842` | `QMessageBox.question` with Yes/No, blocking |
| 13 | Theme following | `ui/ui_helpers.py:100-133` | The one palette read left: `on.palette().color(on.foregroundRole())` after Qt has polished the widget, used to recolour the SVG before rasterising it with `QSvgRenderer` and `QPainter`. Callers re-tint in `showEvent` because construction time is too early (`ui/copilot_panel.py:611-623`, `ui/history_popup.py:75-83`, `ui/upload_widget.py:179-181`). `tests/test_the_panel_wears_the_users_theme.py:62-84` pins it to this one site |
| 14 | FreeCAD preferences as fonts | `ui/ui_helpers.py:149-170` | `FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Editor").GetString("Font")` and the same for the general UI font |
| 15 | Settings and providers as Qt singletons | `utils/settings_manager.py:17-34`, `utils/provider_manager.py:15-42` | Both are `QObject`s with signals; the model combo refreshes on `provider_manager.instance().models_changed` (`ui/copilot_panel.py:559`), and the provider list is fetched on `QThread`s |
| 16 | Session naming thread | `ui/copilot_panel.py:360-373`, `947-958` | A `QThread` per title, kept in a list so Qt does not collect it mid-flight |
| 17 | Popup placement | `ui/history_popup.py:198-205` | `area.mapToGlobal(QPoint(0,0))` and `setFixedWidth(area.width())`: the popup is positioned against the live dock geometry |
| 18 | Shutdown | `ui/copilot_panel.py:1385-1412` | `app.aboutToQuit` → stop the worker, release the waiting question, `pipeline.shutdown()` which kills the headless FreeCAD child process, then `thread.quit()`, `wait(3000)`, `terminate()` |
| 19 | Preflight child process | `core/apply.py:290-303`, `ui/copilot_panel.py:1401-1402` | Every Step is first run in a second, headless FreeCAD owned by the pipeline the panel holds |

Not a coupling, because it does not exist: there is no drag-and-drop anywhere
in `ui/` — no `setAcceptDrops`, `dragEnterEvent` or `dropEvent`. Attachments
arrive by the file dialog or by paste, only.

The panel reads no FreeCAD state directly. `import FreeCAD` at
`ui/copilot_panel.py:22` is unused by any statement in the module, and
`tests/test_panel_renders_the_stream.py:93-102` asserts the panel never names
`ActiveDocument`, `openTransaction`, `commitTransaction`, `PreflightWorker` or
`check`. The FreeCAD reads in `ui/` are `ui/ui_helpers.py:152` and `:164`, both
preference lookups.

## 4. Logic living in the UI layer

354 lines in `ui/` that are not rendering and not Qt. Each would have to exist
on the backend of any UI that is not this one.

| Where | Lines | What the rule is |
| --- | --- | --- |
| `ui/copilot_panel.py:307-359` `LoopWorker.do_work` | 53 | Builds the `EventStream`, attaches the session log, subscribes an error logger, builds the tool registry, names the Document from `snapshot`, resolves the client, calls `run`, turns an unresolvable model and a crash into `error` events |
| `ui/copilot_panel.py:625-677` `_refresh_model_combo` | 53 | Groups models by provider, keeps a stored model that no fetched list has confirmed yet, and only retires it once every provider has answered (`awaiting_model_lists()`) |
| `ui/copilot_panel.py:678-694` `_retire_model` | 17 | Falls back to the preferences default, or row 1; writes the new last-model; says so on the status line |
| `ui/copilot_panel.py:695-702` `_on_model_changed` | 8 | Persists the model and drops the client so the next run rebuilds it |
| `ui/copilot_panel.py:742-753` `_became_the_live_session` | 12 | Drops `worker.pipeline`, which is where Owned Objects live, so a Session cannot inherit another's deletion rights |
| `ui/copilot_panel.py:765-779` `switch_session` | 15 | Refuses to change Session while a run is in flight |
| `ui/copilot_panel.py:780-795` `restore_session` | 16 | Replays the log once per Session; empty log means empty state |
| `ui/copilot_panel.py:796-824` `_replay_chunk` | 29 | Replays `RESTORE_CHUNK = 25` events per event-loop turn, abandons the replay if the user switched away, appends the truncation notice |
| `ui/copilot_panel.py:825-829` `on_document_named` | 5 | Records which Document a Session worked on |
| `ui/copilot_panel.py:861-879` `_unusable_attachments` | 19 | Calls `core.llm.unusable_attachments` against the selected model before the run starts, and keeps the chips on the bar so the offending one can be removed |
| `ui/copilot_panel.py:880-927` `on_send` | 48 | Routes the composer to an open question instead of a new run; gates on unusable attachments; names the Session; takes the chips off the bar; resets render state; emits `request_work` with the outstanding list |
| `ui/copilot_panel.py:928-946` `_name_session` | 19 | Fallback name from the Intent, re-reads the position after pruning, asks for a generated title when one is still wanted |
| `ui/copilot_panel.py:960-963` `_title_arrived` | 4 | Routes a title to the Session that asked for it, by log path |
| `ui/copilot_panel.py:964-977` `on_stop` | 14 | Disables an open question card, stops the worker, which releases the waiting question |
| `ui/copilot_panel.py:1343-1364` `on_run_finished` | 22 | Carries `result.unmet` forward into the next Intent, whatever the run ended as; discards the temp files the run was given |
| `ui/upload_widget.py:122-127` `get_and_clear` | 6 | Handing files to a run empties the bar without deleting them |
| `ui/upload_widget.py:225-238` `discard` | 14 | Deletes only the temp files this widget wrote; a file the user chose is left alone |

A further 45 lines in `ui/multi_tab_manager.py` are delegation to
`core/sessions.py` — `begin` (`165-181`), `wants_a_title` and `title_arrived`
(`182-190`), `rename_session` and `name_document` (`201-207`), `count`,
`get_session_info`, `indexed_sessions` and `mark_restored` (`214-225`). The
rules themselves are already outside Qt (`core/sessions.py:1-19`); these lines
are the call surface plus the signals that keep the stack of chat areas in
step.

## 5. Test coverage

| Area | Covered by | Notes |
| --- | --- | --- |
| Composer sizing | `tests/test_panel_level2.py:79-118` | One row empty, grows, then scrolls |
| Send/Stop exclusivity | `tests/test_panel_level2.py:119-132` | Never both visible |
| Status line width | `tests/test_panel_level2.py:133-153` | A long Step must not widen the dock |
| Empty state | `tests/test_panel_level2.py:154-159` | Asserts on `panel._empty_state.text()` |
| Model combo | `tests/test_panel_level2.py:160-248` | Headings unselectable, stored model kept while a provider is still awaited, a failed fetch does not retire, retiring says so, retiring with no default |
| Attachment hand-off | `tests/test_panel_level2.py:249-302` | What `request_work` carries, and the unusable-attachment gate |
| Clipboard paste | `tests/test_panel_level2.py:303-405` | Real clipboard, pixel assertions, blank-pixmap failure, text paste, temp-file ownership |
| Transcript layout and scrolling | `tests/test_panel_level2.py:406-472` | Wrapped-message height, stays where the user scrolled, follows the bottom |
| Shutdown with a question open | `tests/test_panel_level2.py:473-510` | The hang case |
| Docking | `tests/test_panel_level2.py:511-557` | Installed once on the main window; not torn down on workbench change |
| History popup | `tests/test_panel_level2.py:558-754` | Placement, width, row layout, narrow-row behaviour, tooltip, trash icon drawn and tinted, which Sessions appear, row index identity |
| Session naming and titles | `tests/test_panel_level2.py:755-895` | First Intent names it, once only, generated title replaces the fallback once, lands on the right Session, manual rename wins, retry |
| Restore and replay | `tests/test_panel_level2.py:923-1094` | Whole transcript, unknown kinds skipped, replayed once, chunked at `RESTORE_CHUNK`, short transcript in one go, switching away abandons, truncation notice |
| Session lifecycle | `tests/test_panel_level2.py:1095-1214` | Rename persists, delete takes the log, pruning takes the page, restored Session owns nothing, switching refused mid-run |
| Question cards | `tests/test_panel_level2.py:1215-1303`, `1505-1573` | Question in the transcript, composer answer, empty answer nags, Enter answers instead of starting a run, Stop releases and disables |
| Plan render | `tests/test_panel_level2.py:1304-1327`, `1478-1504`, `1574-1600` | Acceptance list present before the Gate, revised Plan gets its own widget, decided/unanswered/acceptance all open |
| Attachment render | `tests/test_panel_level2.py:1347-1396` | Picture stays after the chips clear; a file that has gone renders its name |
| Document link | `tests/test_panel_level2.py:1397-1477` | The Gate links to the file and does not pour the description into the chat |
| Every kind is shown | `tests/test_panel_renders_the_stream.py:33-60` | Parses `on_event` and runs `narrate` over all 22 kinds, minus `cost` and `design` |
| Panel does not drive the loop | `tests/test_panel_renders_the_stream.py:63-160` | Only `run` may be called; no Document access; `moveToThread`; dock names itself; `on_stop`/`cancelled`; `log_to` |
| Theme | `tests/test_the_panel_wears_the_users_theme.py:49-141` | No `palette(...)` but `palette(highlight)`, one `QPalette` read in Python, hardcoded colours only for success and failure, greys are grey, size rules reset the box |
| Main-thread dispatch | `tests/test_main_thread_dispatch.py:70-141` | Runs on the main thread from a worker, blocks, re-raises, no `singleShot` outside `copilot_panel.py` |
| Viewport | `tests/test_the_viewport_is_the_3d_view.py:72-110` | `fit_view` and the Review capture pick the 3D view, not the tab in front |
| Session rules | `tests/test_sessions.py`, `tests/test_session_title.py` | The half already outside Qt |
| Narration | `tests/test_narrate.py:59-171` | One sample per kind; what is prose and what is not |
| Event stream and log | `tests/test_session_log.py:17-199` | Emit, subscribe, log, replay, truncation, unknown kinds, unserialisable data |
| Gate and Owned Objects, headless | `tests/test_plan_gate.py`, `tests/test_owned_objects.py` | The `gate` and `confirm` callables against the loop and the pipeline, with no widgets |

What has no test and would break silently if the widgets were replaced:

- `CodeBlockWidget` — no test names it. `tool_call` carrying `code` is
  rendered at `ui/copilot_panel.py:1158-1159` and asserted nowhere.
- `TextMessageWidget` as the user's own turn — no test names it; the assertions
  read label text through helpers (`tests/test_panel_level2.py:896-922`).
- `AccordionWidget.start_pulsing` / `stop_pulsing`
  (`ui/accordion_widget.py:62-82`) — no test.
- `PlanWidget.highlight` and `mark` (`ui/plan_widget.py:59-73`), and the
  `step_started` / `applied` / `blocked` marking in
  `ui/copilot_panel.py:1029-1039` — no test asserts a marker or a state.
- `on_plan_gate` (`ui/copilot_panel.py:1165-1187`) — the card with the four
  answers is not exercised; `tests/test_plan_gate.py` tests the loop side of
  the same contract, `on_revision` and `on_missing_values` are exercised.
- `on_owned_objects` (`ui/copilot_panel.py:1236-1256`) —
  `tests/test_owned_objects.py` covers `ApplyPipeline`, not the card.
- `_build_qss` (`ui/copilot_panel.py:41-135`) — checked for what it must not
  contain, never for what it produces.
- `_render_thought` phase grouping (`ui/copilot_panel.py:1064-1073`) — the
  panel's behaviour is named in `tests/test_loop.py:3798-3809`, which asserts
  on the event stream; no test opens an accordion per phase.
- `HistoryPopup.filter_items` and `search_input` (`ui/history_popup.py:133-144`,
  `191-196`) — neither name appears in `tests/`.

## 6. Measurement

| | Lines | Share |
| --- | ---: | ---: |
| Total `ui/` | 2606 | 100% |
| Pure rendering | 1683 | 64.6% |
| Logic or Qt/FreeCAD coupling | 923 | 35.4% |

The 923 breaks down as 354 lines of logic (§4), 524 lines of Qt or FreeCAD
coupling (§3), and 45 lines of delegation into `core/sessions.py`.

Per file:

| File | Total | Rendering | Logic | Coupling | Delegation |
| --- | ---: | ---: | ---: | ---: | ---: |
| `ui/copilot_panel.py` | 1412 | 785 | 334 | 293 | 0 |
| `ui/multi_tab_manager.py` | 229 | 179 | 0 | 5 | 45 |
| `ui/upload_widget.py` | 238 | 167 | 20 | 51 | 0 |
| `ui/history_popup.py` | 205 | 182 | 0 | 23 | 0 |
| `ui/ui_helpers.py` | 170 | 102 | 0 | 68 | 0 |
| `ui/chat_widgets.py` | 121 | 98 | 0 | 23 | 0 |
| `ui/plan_widget.py` | 91 | 86 | 0 | 5 | 0 |
| `ui/accordion_widget.py` | 89 | 84 | 0 | 5 | 0 |
| `ui/copilot_dock.py` | 51 | 0 | 0 | 51 | 0 |
| **Total** | **2606** | **1683** | **354** | **524** | **45** |

`ui/copilot_panel.py` holds every line of logic in the package except 20 in
`ui/upload_widget.py`, and 56% of the coupling.
