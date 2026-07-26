# FreeCAD 1.x — Addon / Workbench Developer Reference

Companion to `freecad_scripting.md` (that file = modelling API for generated scripts; this file = host-platform facts for the addon itself).

Every claim below links to a primary source: the FreeCAD source tree on GitHub, the official wiki mirror repo `FreeCAD/FreeCAD-documentation`, or a direct probe of the FreeCAD 1.0.2 build installed on this machine.

## 0. Source-access caveat (read first)

- `wiki.freecad.org` is behind an Anubis bot-check and **cannot be fetched programmatically**. All wiki claims here come from the project's own Markdown mirror: [FreeCAD/FreeCAD-documentation](https://github.com/FreeCAD/FreeCAD-documentation/tree/main/wiki).
- That mirror is **stale for 1.1**: [`wiki/Release_notes_1.1.md`](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Release_notes_1.1.md) is still the pre-release placeholder ("under development", last commit `2025-01-13`, "release 1.0 upgrade"), while 1.1.0 actually shipped 2026-03-25. **The official 1.1 "Removed/Changed Python API" list is therefore UNVERIFIED here.** 1.1 statements below are derived from the source tree at tag `1.1.3`.

## 1. Versions and dates

| Version | Released | Source |
|---|---|---|
| 1.0 | 2024-11-18 | [Release_notes_1.0.md](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Release_notes_1.0.md) |
| 1.1.0 | 2026-03-25 | [GH release `1.1.0`](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.0) |
| 1.1.3 | 2026-07-23 / 2026-07-25 (latest stable at time of writing) | [GH releases](https://github.com/FreeCAD/FreeCAD/releases) |

- Locally installed build (probed with `freecadcmd`): `App.Version()` → `['1','0','2','39319 (Git)', ...]` — **this machine runs FreeCAD 1.0.2, not 1.1**.

## 2. Python version

- **1.0 build minimum: Python 3.8** — [`SetupPython.cmake` @1.0.0](https://github.com/FreeCAD/FreeCAD/blob/1.0.0/cMake/FreeCAD_Helpers/SetupPython.cmake): `if (${PYTHON_VERSION_STRING} VERSION_LESS "3.8") message(FATAL_ERROR ...)`.
- **1.1 build minimum raised to Python 3.10** — [`SetupPython.cmake` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/cMake/FreeCAD_Helpers/SetupPython.cmake): `if (${Python3_VERSION} VERSION_LESS "3.10") message(FATAL_ERROR "To build FreeCAD you need at least Python 3.10")`.
- Bundled interpreter is a *packaging* choice, not a source constant. The local 1.0.2 macOS conda bundle ships **Python 3.11.13** (`Contents/Resources/conda-meta/python-3.11.13-*.json`; probe printed `PY 3.11.13`). Do not hard-code; read `sys.version_info`.
- Consequence for `__getstate__`/`__setstate__` — see §4.

## 3. Qt / PySide — the biggest addon-facing risk

### 3.1 Qt version is a build option, and the default flipped in 1.1

- `FREECAD_QT_VERSION` accepts `Auto|5|6`. Under `Auto`:
  - **1.0 prefers Qt5**: `find_package(Qt5 ...)` first, falls back to Qt6 — [`ChooseQtVersion.cmake` @1.0.0](https://github.com/FreeCAD/FreeCAD/blob/1.0.0/cMake/FreeCAD_Helpers/ChooseQtVersion.cmake).
  - **1.1 prefers Qt6**: `find_package(Qt6 ...)` first, falls back to Qt5 — [`ChooseQtVersion.cmake` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/cMake/FreeCAD_Helpers/ChooseQtVersion.cmake).
- PySide/Shiboken major version tracks Qt major: Qt5 → PySide2/Shiboken2, else `PYSIDE_MAJOR_VERSION = FREECAD_QT_MAJOR_VERSION` — [`SetupShibokenAndPyside.cmake` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/cMake/FreeCAD_Helpers/SetupShibokenAndPyside.cmake).
- Both bindings are supported in C++ at once (`SbkPySide2_*` / `SbkPySide6_*`, `ModulePySide = "PySide2"|"PySide6"`) — [`PythonWrapper.cpp` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Gui/PythonWrapper.cpp) — but only **one** is active in a given build.

### 3.2 The `PySide` shim is the only version-agnostic import

- FreeCAD **generates** a `PySide` package at build time and installs it to `<install>/Ext/PySide` (macOS non-conda: `MacOS/PySide`) — [`SetupShibokenAndPyside.cmake` @1.1.3, lines 84–124](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/cMake/FreeCAD_Helpers/SetupShibokenAndPyside.cmake#L84-L124). Generated modules:
  - `PySide/__init__.py`, `QtCore.py`, `QtNetwork.py`, `QtGui.py`, `QtSvg.py`, `QtUiTools.py`, `QtWidgets.py`, `QtSvgWidgets.py`, `QtWebEngineWidgets.py`.
  - Each is literally `from PySide{2|6}.<Module> import *`.
  - **`PySide.QtGui` re-exports `PySide*.QtWidgets` too**, plus the compat alias `QHeaderView.setResizeMode = QHeaderView.setSectionResizeMode`.
  - **`PySide.QtWidgets` exists** (a real module, not an alias to QtGui).
- Verified on disk in the local 1.0.2 bundle: `Contents/Resources/Ext/PySide/QtGui.py` contains `from PySide2.QtGui import *` / `from PySide2.QtWidgets import *`.
- FreeCAD's own Python modules use `from PySide import ...` (≈333 files in `FreeCAD/FreeCAD` @main). Example wrapper pattern in the Addon Manager: [`addonmanager_pyside_interface.py` @1.0.0](https://github.com/FreeCAD/FreeCAD/blob/1.0.0/src/Mod/AddonManager/addonmanager_pyside_interface.py).
- **Rule: import `from PySide import QtCore, QtGui, QtWidgets`. Never import `PySide2`/`PySide6` directly** — a direct `PySide2` import fails outright on a Qt6 build, and objects returned by FreeCAD (`Gui.getMainWindow()` etc.) are Shiboken-wrapped for the *build's* binding, so mixing bindings cannot work even if both are installed.

### 3.3 Qt6 API removals that hit typical addon code

Not FreeCAD-specific, but they land when a user runs a Qt6 build. Verify against Qt docs before relying on: `QtWidgets.qApp` (removed in PySide6), `QRegExp`, `QAction`/`QShortcut` moved `QtWidgets`→`QtGui`, `QDesktopWidget`, high-DPI attributes. **Not independently verified here against a primary Qt source** — flagged as a migration checklist, not a claim.

## 4. FreeCAD 1.0 — changes that matter to addons

From [Release_notes_1.0.md](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Release_notes_1.0.md), section *Core system and API*:

- **`Support` → `AttachmentSupport`.** The property holding an attachment's reference object(s) was renamed ([PR #12714](https://github.com/FreeCAD/FreeCAD/pull/12714)). The file-format `Attachment` property was renamed too, breaking forward-compat with ≤0.21.
  - Source: `handleChangedPropertyName()` maps a legacy `"Support"` property **only when restoring old documents** — [`AttachExtension.cpp` @1.1.3, ~L497–L510](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Mod/Part/App/AttachExtension.cpp#L497-L510). There is no live `Support` attribute.
  - Probed on 1.0.2: a `Sketcher::SketchObject`'s `PropertiesList` contains `AttachmentSupport` and `MapMode`; contains neither `Support` nor `Plane`.
- **`__getstate__`/`__setstate__` → `dumps`/`loads`** for custom data on Python features, forced by Python 3.11 internals ([PR #10769](https://github.com/FreeCAD/FreeCAD/pull/10769), [PR #12243](https://github.com/FreeCAD/FreeCAD/pull/12243)).
- New: `getUpDirection()` on `View3DInventor` ([PR #10060](https://github.com/FreeCAD/FreeCAD/pull/10060)).
- New core container `App::VarSet` for user-defined properties ([PR #12135](https://github.com/FreeCAD/FreeCAD/pull/12135)) — the sanctioned replacement for "stash parameters on the document".
- Topological naming mitigation landed ([PR #13705](https://github.com/FreeCAD/FreeCAD/pull/13705)) — generated scripts that reference `Face3`/`Edge7` by string are less fragile than in 0.21 but still not guaranteed stable.
- Task panel is now a stand-alone dock widget, no longer nested in the Combo view ([PR #10681](https://github.com/FreeCAD/FreeCAD/pull/10681)) — affects any code that assumed the old layout.
- Preferences dialog switched from tabs to a tree view ([PR #11018](https://github.com/FreeCAD/FreeCAD/pull/11018)).
- Safe mode (`Help → Restart in safe mode`) disables all addons ([PR #16858](https://github.com/FreeCAD/FreeCAD/pull/16858)) — user bug reports may come from a session where the addon never loaded.
- Start **Workbench** was replaced by a non-workbench Start page ([PR #13134](https://github.com/FreeCAD/FreeCAD/pull/13134)).
- No Python API *removals* are listed for 1.0.

## 5. FreeCAD 1.1 — what is verifiable from source

Wiki release notes unavailable (§0). Verified from the tree:

- `FreeCADGui` module-level Python API: **nothing removed** between 1.0.0 and 1.1.3; 5 additions — `doCommandEval`, `doCommandSkip`, `resumeWaitCursor`, `suspendWaitCursor`, `showPreferencesByName`. (Diff of the `PyMethodDef` tables in [`ApplicationPy.cpp` @1.0.0](https://github.com/FreeCAD/FreeCAD/blob/1.0.0/src/Gui/ApplicationPy.cpp) vs [@1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Gui/ApplicationPy.cpp).)
- **Legacy capitalised `PythonWorkbench` methods are now `@deprecated`**: `AppendMenu`, `RemoveMenu`, `ListMenus`, `AppendContextMenu`, `RemoveContextMenu`, `AppendToolbar`, `RemoveToolbar`, `ListToolbars`, `AppendCommandbar`, `RemoveCommandbar`, `ListCommandbars`. Use the lowercase forms (`appendMenu`, `appendToolbar`, `appendContextMenu`, …) — [`PythonWorkbench.pyi` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Gui/PythonWorkbench.pyi).
- API surface is being documented as `.pyi` stubs in 1.1 (`src/Gui/*.pyi`, `src/App/Metadata.pyi`); these files did **not** exist at 1.0.0 (404). Useful as the authoritative signature reference.
- **The Addon Manager is now a git submodule**, developed in its own repo: `src/Mod/AddonManager` → <https://github.com/FreeCAD/AddonManager.git> ([`.gitmodules` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/.gitmodules)). File Addon-Manager issues there, not against FreeCAD.
- Sketcher: external geometry is real (defining) geometry by default in 1.1, and new `Projection` / `Intersection` tools exist ([PR #17736](https://github.com/FreeCAD/FreeCAD/pull/17736); placeholder-era mirror text, corroborated by the PR).
- PartDesign origin/datum planes were rebuilt on new core datums; **1.1-created files can break in 1.0 and older files are converted on open** ([PR #18126](https://github.com/FreeCAD/FreeCAD/pull/18126)).
- Core Datum objects (coordinate systems, datum plane/line/point) added as app-level features ([PR #18332](https://github.com/FreeCAD/FreeCAD/pull/18332)).

## 6. Addon packaging

Reference: [Package_Metadata.md](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Package_Metadata.md) (schema, format version 1, modelled on ROS REP-149) and the C++ parser [`src/App/Metadata.h` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/App/Metadata.h).

### 6.1 Directory layout

- User Mod dir on macOS: `~/Library/Application Support/FreeCAD/Mod/<AddonName>/` (Linux `~/.local/share/FreeCAD/Mod/`, Windows `%APPDATA%\FreeCAD\Mod\`) — [Workbench_creation.md](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Workbench_creation.md).
- `Init.py` runs on every start (console + GUI); `InitGui.py` runs immediately after, GUI only.
- External workbenches must be **Python-only** (C++ workbenches must be merged upstream).
- `package.xml` must sit at the repo root of the **primary branch**; only that branch's copy is read.
- Content items imply subdirectories named after them; `<subdirectory>./</subdirectory>` opts out (legacy flat layout).

### 6.2 `package.xml` schema (format="1")

Root: `<package format="1" xmlns="https://wiki.freecad.org/Package_Metadata">` (namespace must be exact).

Required: `name`, `version`, `date`, `description`, ≥1 `maintainer` (with `email` attr), ≥1 `license` (SPDX id; optional `file` attr), `icon`, `content`.
Optional: `url` (multi; `type` = website|bugtracker|repository|readme|documentation|discussion; `branch` required for `repository`), `author`, `depend`, `conflict`, `replace`, `tag`, `freecadmin`, `freecadmax`, `pythonmin`.

- `version`: semver 2.0 **or** CalVer, not both, never blank; an increase is what triggers "update available".
- `date`: `YYYY-MM-DD` or `YYYY.MM.DD`.
- Inside `<content>`, FreeCAD recognises `workbench`, `macro`, `preferencepack`. For `workbench`, `<classname>` (the `InitGui.py` class whose `Icon` member is read) and `<icon>` are **required**.
- `depend` attributes: `version_lt|version_lte|version_eq|version_gte|version_gt`, `condition` (FreeCAD Expression over `$BuildVersionMajor`, `$BuildVersionMinor`, `$BuildRevision`), `optional="true|false"`, `type="automatic|addon|internal|python"`.
- `freecadmin` / `freecadmax` / `pythonmin` are parsed as `Meta::Version` — [`Metadata.h` @L264-L266](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/App/Metadata.h#L264-L266). `pythonmin` blocks installation on older Python; only the minor number is compared.
- Unrecognised tags are ignored, not an error. Paths always use `/`.
- Alternatives to `package.xml` for deps only: `metadata.txt` (`workbenches=`, `pylibs=`, `optionalpylibs=`) or `requirements.txt` (names only — pip options and version pins are ignored).
- Optional `uninstall.py` at the addon root runs on Addon-Manager uninstall.
- Validation: enable Addon Manager developer mode (`BaseApp/Preferences/Addons/developerMode` = true) to have package.xml files checked.

### 6.3 Python dependencies / pip target

- Addon Manager installs pip deps with `--target <UserAppData>/AdditionalPythonPackages/py{major}{minor}` — [`addonmanager_utilities.get_pip_target_directory()`](https://github.com/FreeCAD/AddonManager/blob/main/addonmanager_utilities.py#L566-L581), same in [1.0.0](https://github.com/FreeCAD/FreeCAD/blob/1.0.0/src/Mod/AddonManager/addonmanager_utilities.py#L358-L364).
- FreeCAD adds **both** `AdditionalPythonPackages/py{major}{minor}` **and** the flat `AdditionalPythonPackages` to `sys.path` at startup — [`FreeCADInit.py` @1.1.3, L188-L198](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/App/FreeCADInit.py#L188-L198). The versioned dir is the canonical one; the flat dir is a legacy fallback.
- Correct interpreter for `pip` subprocesses: `from freecad.utils import get_python_exe` — [`src/Ext/freecad/utils.py` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Ext/freecad/utils.py). Resolution order: `BaseApp/Preferences/PythonConsole/PathToPythonExecutable` → `<FreeCADHome>/bin/python3` → `<FreeCADHome>/bin/python` → `shutil.which("python3")` → `shutil.which("python")` → `""`.

## 7. Python API surface used by this addon (verified)

Registered `FreeCADGui` methods, from the `PyMethodDef` table in [`ApplicationPy.cpp` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Gui/ApplicationPy.cpp):

`SendMsgToActiveView activateView activateWorkbench activeDocument activeView activeWorkbench addCommand addDocumentObserver addIcon addIconPath addLanguagePath addModule addPreferencePage addResourcePath addWorkbench addWorkbenchManipulator createDialog createViewer doCommand doCommandEval doCommandGui doCommandSkip editDocument export getDocument getIcon getLocale getMainWindow getMarkerIndex getUserEditMode getWorkbench hide hideObject insert isIconCached listUserEditModes listWorkbenches loadFile open reload removeDocumentObserver removeWorkbench removeWorkbenchManipulator resumeWaitCursor runCommand sendMsgToFocusView setActiveDocument setLocale setUserEditMode show showDownloads showObject showPreferences showPreferencesByName supportedLocales suspendWaitCursor updateGui updateLocale`

- `addPreferencePage(path|dialog, group)` — accepts either a `.ui` path or a Python dialog class; call it **once** or the page is added repeatedly ([Workbench_creation.md](https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Workbench_creation.md)). FreeCAD looks for a group icon named `preferences-<group>.svg` in known icon paths (extend via `addIconPath`).
- `addCommand(name, cmdObject)` — command object supplies `GetResources()` → dict with `Pixmap`, `MenuText`, `ToolTip`, optional `Accel`; `Activated()`; optional `IsActive()`.
- Python workbench class: `Initialize()` (once, on first activation), `Activated()`, `Deactivated()`, `ContextMenu(recipient)` where recipient is `"view"` or `"tree"`, and `GetClassName()` which must return exactly `"Gui::PythonWorkbench"`. Register with `Gui.addWorkbench(MyWB())`. Menus/toolbars via `self.appendToolbar(name, cmdlist)` / `self.appendMenu(name, cmdlist)`.
- Task panels: `Gui.Control.showDialog(dialog)`, `activeDialog()`, `activeTaskDialog()`, `closeDialog()`, `addTaskWatcher()`, `showTaskView()`, `showModelView()` — [`Control.cpp` @1.1.3](https://github.com/FreeCAD/FreeCAD/blob/1.1.3/src/Gui/Control.cpp), typed in [`FreeCADGui._Control.pyi` @main](https://github.com/FreeCAD/FreeCAD/blob/main/src/Gui/FreeCADGui._Control.pyi).
- Document transactions (probed on 1.0.2): `openTransaction`, `commitTransaction`, `abortTransaction`, `undo`, `recompute` all present on `App.Document`.
- `body.newObject('Sketcher::SketchObject', 'Sketch')` — present on `PartDesign::Body` (probed on 1.0.2).

## 8. Things that will bite *this* addon

1. **`FreeCADGui.addTodo` does not exist.** `core/command_executor.py` tries it first for main-thread dispatch. It is absent from the `FreeCADGui` method table in 1.0.0 and 1.1.3, and `strings` on the shipped `libFreeCADGui.dylib` (1.0.2) finds `addWorkbench` but **zero** matches for `addTodo`. The `hasattr` guard means it silently falls through to the `QTimer.singleShot` path — that path is the real mechanism. FreeCAD's own equivalent is `src/Mod/Draft/draftutils/todo.py` (`ToDo`), also QTimer-based.
2. **Direct `PySide2` imports.** `ui/*.py` and `core/command_executor.py` do `from PySide2 import ...` with a `from PySide import QtCore, QtGui; QtWidgets = QtGui` fallback. On a Qt6/PySide6 build (the **default** for 1.1 — §3.1) the `PySide2` branch raises `ImportError`, and the fallback then loses the real `QtWidgets` module. Fix: import `from PySide import QtCore, QtGui, QtWidgets` unconditionally — all three modules exist in both the Qt5 and Qt6 shims (§3.2). Also drop `QtWidgets.qApp` (`command_executor.py`), which is PySide2-only; use `QtWidgets.QApplication.instance()`.
3. **No `package.xml`.** The repo root has `Init.py`/`InitGui.py` but no `package.xml`, so the addon is not installable or updatable via the Addon Manager, and cannot declare `pythonmin`, `freecadmin`, or its pip dependencies. §6.2 has the minimum schema; `<classname>CopilotWorkbench</classname>` + `<subdirectory>./</subdirectory>` matches the current flat layout.
4. **pip target directory.** `core/dependency_manager.py` installs into `getUserAppDataDir()/AdditionalPythonPackages` (flat). FreeCAD does add that path to `sys.path`, so it works — but the Addon-Manager-canonical path is `AdditionalPythonPackages/py{major}{minor}`, which keeps installs from colliding when a user upgrades to a FreeCAD built against a different Python. The `get_python_exe` usage is already correct.
5. **`Gui.Workbench` menu/toolbar methods.** If the workbench ever calls `AppendToolbar`/`AppendMenu` (capitalised), those are `@deprecated` as of 1.1 — use lowercase.
6. **Sketch positioning.** `freecad_scripting.md` says `Support`/`Plane` are "completely REMOVED" — accurate as of 1.0 (probe confirms neither is in `PropertiesList`), but the replacement is not only `Placement`: attachment now goes through **`AttachmentSupport` + `MapMode`**. Consider updating that file so generated code can attach sketches to faces/datums instead of only hard-positioning them.
7. **`doc.Length = 50` really does fail.** Probed on 1.0.2: `AttributeError: 'App.Document' object has no attribute 'Length'`. The 1.0-sanctioned parametric container is `App::VarSet` (§4), not Python locals, if persistence is ever wanted.
8. **`workbench.Activated()` re-docks on every activation.** `Mod/CopilotWorkbench.py` calls `mw.addDockWidget(...)` each time; with the 1.0 task-panel/overlay rework the user's docking choices are more visible, so re-adding on every switch may fight the user's layout. Not a FreeCAD API break — behavioural note only.

## 9. Explicitly NOT verified

- The official FreeCAD 1.1 "Removed Python API" / "Changed Python API" wiki lists (wiki unreachable, mirror stale — §0). Treat §5 as a lower bound on 1.1 changes.
- Which Qt/PySide and Python the **official 1.1 binaries** ship. Source defaults say Qt6-preferred; only the local 1.0.2 conda bundle (Qt 5.15.15 / PySide2 5.15.15 / Python 3.11.13) was inspected directly.
- Qt5→Qt6 API removals in §3.3 (stated as a checklist from general Qt knowledge, not from a Qt primary source).
- FreeCAD 1.2 / `main`-branch behaviour beyond the `.pyi` stubs cited.
