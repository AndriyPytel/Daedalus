# Where a React UI could actually run inside a FreeCAD addon

*Research note, primary sources only: the FreeCAD source tree, the conda-forge package index, the
PyPI metadata API, `doc.qt.io`, and FreeCAD's own AddonManager code as shipped in the macOS bundle.
Every claim carries the URL or the file path of the source that owns it. Measurements taken on this
machine — FreeCAD 1.x macOS arm64, PySide6 6.8.3 / Qt 6.8.3 — are marked **[measured]** and are
reproduced by the scripts in §8.*

## The question

The Copilot UI is a `QDockWidget` in the FreeCAD main window (`ui/copilot_dock.py`). If that UI were
a React app instead of Qt widgets, **where would the HTML actually be rendered**, on macOS, Windows
and Linux, and what does each answer cost?

## Executive summary

- **There is no `QWebEngineView` in FreeCAD 1.x on any platform.** Commit `c35693fd` (2024-04-19),
  *"Web: Remove GUI … Dependency on QtWebWengine has been removed"*, deleted it; `SetupQt.cmake` and
  `src/Gui/CMakeLists.txt` lost every `WebEngine` line between 0.21.2 and 1.0.0, and the wiki says
  *"The Web Workbench is no longer included after version 0.21."* macOS is not the exception — the
  Windows LibPack builds Qt without `qtwebengine`, and conda-forge's `pyside6` has never depended on
  `qt6-webengine` on any subdir, up to and including 6.11.1.
- **Route A (pip a browser in) is impossible, three times over.** `PySide6` is a regular package, so
  FreeCAD's bundled copy always wins `sys.path` (the AddonManager's vendor dir is appended
  `FallbackLast`); forcing pip's copy in puts two Qt 6.8.3 runtimes in one process — the failure
  Werner Mayer diagnoses in forum t=75566 and that broke an AppImage in t=106855; and Qt requires
  WebEngine to be initialised **before `QApplication` exists**, which an addon runs after. The
  AddonManager also refuses it by name: `if pymod.lower().startswith("pyside"): continue`.
- **Route C (QtWebView / QtQuick) is the same wheel and the same trap.** `QtWebView` ships only in
  `PySide6-Addons`, needs `QtWebView::initialize()` before `QGuiApplication`, and on Linux "depends
  on the Qt WebEngine module to render content" anyway.
- **Route B works today, everywhere, for 0 MB.** Measured in FreeCAD's own interpreter: a
  `QTcpServer` and a `QWebSocketServer` on FreeCAD's Qt event loop, `HTTP/1.1 200 OK` in 2.99 ms,
  handlers on the **main thread** — so FreeCAD's API can be called directly, with no marshalling.
- **Route D buys no dock.** FreeCAD's own Render addon already runs its web view out of process; on
  macOS embedding its window crashes FreeCAD (`libshiboken: Overflow … winId`), and the open fix is
  to stop embedding.
- **Nobody has done React in FreeCAD.** Across both official indexes (9 131 files) there is no
  React/Vue/Vite/webpack anywhere; the four addons that ever embedded a browser are archived,
  delisted, degraded to the system browser, or moved out of process. Every FreeCAD chat UI in
  existence is `QTextBrowser.setHtml()`.
- **The dock cannot be kept by any web route.** So the real choice is not "dock vs. no dock" — it is
  *which* out-of-window renderer, and the cheapest by three orders of magnitude is the browser the
  user already has.

## 1. The premise to correct first: FreeCAD 1.0 dropped Qt WebEngine on *every* platform

The starting assumption for this note was that macOS is the odd one out — that Windows and Linux
have `QWebEngineView` and the macOS bundle is a degraded build. **That is not what the source says.**
Qt WebEngine was removed from FreeCAD deliberately, in one commit, for all platforms.

**The commit.** `c35693fd`, 2024-04-19, *"Web: Remove GUI"* **[measured]**:

> Web no longer provides a user-visible browser, but only a simple server for remote interaction
> with FreeCAD. Dependency on QtWebWengine [sic] has been removed.

**The build no longer asks for it.** In 0.21.2 the Qt component list was conditional on the module:

```cmake
# cMake/FreeCAD_Helpers/SetupQt.cmake @ 0.21.2
if (BUILD_WEB)
list (APPEND FREECAD_QT_COMPONENTS WebEngineWidgets)
```

The same file at `1.0.0` and at `main` contains **zero** occurrences of `Web` or `WebEngine`. So does
`src/Gui/CMakeLists.txt`, which at 0.21.2 still carried
`if (QtWebEngineWidgets_FOUND AND BUILD_WITH_CONDA) add_definitions(-DQTWEBENGINE)`.

**The Web workbench's GUI no longer exists in the source.** `src/Mod/Web/CMakeLists.txt` at `1.0.0`,
`1.0.1`, `1.1.0` and `main` is the same five lines — `add_subdirectory(App)` and a copy rule for
`Init.py`. There is no `Gui` subdirectory and no `InitGui.py`: both 404 from
`raw.githubusercontent.com` at every 1.x tag and both are present at `0.21.2`
(`src/Mod/Web/Gui/CMakeLists.txt`, 1661 bytes; `src/Mod/Web/InitGui.py`, 2424 bytes). **[measured]**
The FreeCAD wiki says it in one line: *"The Web Workbench is no longer included after version 0.21."*
([Web Workbench](https://wiki.freecad.org/Web_Workbench))

The `BUILD_WEB` option is still declared `ON` in `InitializeFreeCADBuildOptions.cmake` at every tag
including `main` — but what it now builds is only `src/Mod/Web/App`, headless.

**So the macOS "stub" is the whole Web module, everywhere.** `Mod/Web/Init.py` with no `InitGui.py`
is not a macOS packaging accident; it is what FreeCAD 1.x ships on Windows and Linux too. What
survived is `Web.so` — **[measured]** importable in FreeCAD's own interpreter, exposing exactly
`registerServerFirewall`, `startServer`, `waitForConnection`, with
`startServer(address=127.0.0.1, port=0)`. That is a socket server, not a renderer, and §3 returns to
it because it is the closest thing FreeCAD has to an official blessing of Route B.

**The 1.0 Start page was rewritten so that it could go.** `src/Mod/Start` at 0.21.2 built a
`StartPage` subdirectory of HTML; at `1.0.0` and `main` the module carries
`Copyright (c) 2024 The FreeCAD Project Association AISBL` and no HTML page — its README states it
exists "to eliminate FreeCAD's dependency on QtWebEngine". The largest in-tree consumer was removed
first, which is what made removing the engine possible.

### Why the packaged builds could not have shipped it anyway

FreeCAD's binaries are conda packages, and this bundle proves it: `Contents/Resources/conda-meta`
holds 266 package records, among them `pyside6-6.8.3-py311h8511715_0` (subdir `osx-arm64`) and
`qt6-main-6.8.3-hc9525f7_4`. **[measured]** Four facts follow, none of them macOS-specific:

- **conda-forge's `pyside6` has never carried QtWebEngine bindings, on any platform.** The record in
  this bundle lists its dependencies as `qt6-main 6.8.3.*` and nothing else; its 368 files contain
  zero `webengine` and zero `webview` entries. **[measured]** Nor is this a stale build: the current
  `pyside6` **6.11.1** on conda-forge lists no `qt6-webengine` dependency on `linux-64`, `win-64`,
  `osx-64`, `osx-arm64` or `linux-aarch64` either.
- **`qt6-main` is not a WebEngine package.** Of its 9463 files, the only 12 matching `webengine` are
  `share/qt6/translations/qtwebengine_*.qm` — translation catalogues for a library that is not there.
  **[measured]** They are why a `find` over the bundle looks misleadingly promising.
- **A `qt6-webengine` conda package exists, but not for Qt 6.8.** conda-forge's `qt6-webengine`
  starts at **6.10.2**; builds are 6.10.2, 6.11.0, 6.11.1 for `linux-64`, `linux-aarch64`,
  `osx-arm64`, `win-64` — 89.8 MB on `osx-arm64`, 129.8 MB on `linux-64`, 189.3 MB on `win-64`.
  **[measured]** There is no 6.8.3: when FreeCAD 1.x was built against Qt 6.8.3 the package did not
  exist to depend on.
- **The Windows LibPack builds Qt without `qtwebengine`** — so the Windows installer is in the same
  position as the macOS bundle, for the same reason.

And the packaged application confirms the intent: **conda-forge `freecad` 1.1.3 depends on `pyside6`
and `qt6-main >=6.11.1` and on no WebEngine package — on `win-64` exactly as on `osx-arm64`,
`osx-64`, `linux-64` and `linux-aarch64`.** **[measured]**

The consequence the rest of this note is built on:

> On FreeCAD 1.x there is no `QWebEngineView` to dock, on any of the three platforms. Getting one is
> not "use the Windows build"; it is Route A everywhere, and the project removed it on purpose.

## 2. Route A — pip a browser into FreeCAD's Python

The idea: `pip install PySide6-Addons==6.8.3` into FreeCAD's interpreter, `import
PySide6.QtWebEngineWidgets`, drop a `QWebEngineView` into the existing `QDockWidget`. Matching 6.8.3
exactly is necessary and nowhere near sufficient. Route A fails on four independent counts; any one
of the first three ends it.

### 2.1 The import can never resolve — `PySide6` is a regular package and FreeCAD's copy wins

`PySide6-Addons` 6.8.3 does hold what we want: its PyPI description lists 38 modules including
`QtWebEngineCore`, `QtWebEngineQuick`, `QtWebEngineWidgets` — and also `QtWebView`, `QtHttpServer`
and `QtAsyncio`, all of which recur below. Its `requires_dist` is a hard pin,
`shiboken6==6.8.3` **and** `PySide6-Essentials==6.8.3`: the WebEngine binding cannot be taken alone.

`PySide6` ships an `__init__.py`, so it is a **regular package, not a namespace package**
**[measured]**. Python therefore binds `PySide6` to the *first* matching directory on `sys.path` and
never looks in a second — two `PySide6` directories do not merge. And the AddonManager installs with
`pip install --target <vendor_path>` into
`~/Library/Application Support/FreeCAD/AdditionalPythonPackages/py311`
(`addonmanager_utilities.py::get_pip_target_directory`) **[measured]**, a directory FreeCAD
**appends to the tail** of `sys.path`: the shipped `libFreeCADApp.dylib` contains the literal
`sys.path.append(vendor_path)`, and upstream
[`src/App/FreeCADInit.py`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/App/FreeCADInit.py)
adds it with `sys_path=PathPriority.FallbackLast`.

So the bundled `PySide6` always shadows the pip one, and `import PySide6.QtWebEngineWidgets` raises
`ModuleNotFoundError` — which is exactly what the user in
[forum t=40349](https://forum.freecad.org/viewtopic.php?t=40349) hit. The only way to change that
answer is to write pip's `PySide6` *into the application bundle itself*, on top of FreeCAD's.

### 2.2 …and if you do force it in, you get two Qt runtimes in one process

The two builds are not two copies of one thing. Reading the wheels' zip central directories over HTTP
range requests — no download, no install — against the bundle on disk: **[measured]**

| | pip wheel | FreeCAD's conda build |
|---|---|---|
| widget binding | `PySide6/QtWidgets.abi3.so` | `PySide6/QtWidgets.cpython-311-darwin.so` |
| PySide runtime | `PySide6/libpyside6.abi3.6.8.dylib` | `@rpath/libpyside6.cpython-311-darwin.6.8.dylib` |
| shiboken runtime | `shiboken6/libshiboken6.abi3.6.8.dylib` | `@rpath/libshiboken6.cpython-311-darwin.6.8.dylib` |
| Qt itself | `PySide6/Qt/lib/QtCore.framework/…` — 69 frameworks in `Addons`, 133 across both wheels | `Resources/lib/libQt6*.dylib`; **no `PySide6/Qt` directory at all** |

`EXTENSION_SUFFIXES` puts `.cpython-311-darwin.so` ahead of `.abi3.so`, so after an overwrite the
conda `QtCore` still wins the import while pip's `QtWebEngineWidgets.abi3.so` loads anyway and drags
its own `libpyside6`, `libshiboken6` and `QtCore`/`QtGui`/`QtWidgets` in behind it. Two Qt 6.8.3
runtimes, two shiboken type registries, two sets of `QMetaType` ids in one process. FreeCAD's
`QMainWindow` is then not a `QWidget` the wheel's `QWebEngineView` can accept as a parent.

This is not theoretical. FreeCAD's core maintainer Werner Mayer diagnoses precisely this suffix mix
in [forum t=75566](https://forum.freecad.org/viewtopic.php?t=75566), from a `SIGSEGV`:

> `QtWebEngineWidgets.__file__` `'…/.local/lib/python3.10/site-packages/PySide2/QtWebEngineWidgets.abi3.so'`
> Somehow your system got messed up. The path should be `'…/PySide2/QtWebEngineWidgets.cpython-310-x86_64-linux-gnu.so'`.
> Maybe you once installed that PySide version with pip? … rename the directory to avoid to pull in
> any of the problematic dependencies.

And it has already happened *through the AddonManager itself*:
[forum t=106855](https://forum.freecad.org/viewtopic.php?t=106855) reports that updating Python
dependencies installed a newer PySide6/shiboken6 into an AppImage, after which "FreeCAD loads the
user-installed PySide6 instead of the bundled version… The application starts with missing menus and
toolbars, and several workbenches fail to initialize." The AddonManager's own maintainer, Chris
Hennes, in [t=84500](https://forum.freecad.org/viewtopic.php?t=84500): *"I never did get a
pip-installed PySide6 to work properly."* **Zero success reports were found; every report is a
breakage report.**

### 2.3 The deeper blocker: WebEngine must be initialised *before* `QApplication` exists

Qt requires `QtWebEngineQuick::initialize()` / `Qt::AA_ShareOpenGLContexts` to be set **before the
`QGuiApplication` is constructed** — from
[qtwebenginequickglobal.cpp](https://raw.githubusercontent.com/qt/qtwebengine/6.8/src/webenginequick/api/qtwebenginequickglobal.cpp):

> Sets up an OpenGL Context that can be shared between threads. **This has to be done before
> QGuiApplication is created** … same effect as setting the `Qt::AA_ShareOpenGLContexts` attribute.

An addon is imported into a FreeCAD that constructed its `QApplication` long ago; there is no moment
in an addon's life when that call is still legal. On Linux and Windows this is fatal or degrading —
`getGLType()` reports *"WebEngineContext is used before QtWebEngineQuick::initialize() or OpenGL
context creation failed"*. On macOS both branches are short-circuited by the `!Q_OS_MACOS` / ANGLE
guards, so the blocker does not bite. **Route A is therefore least impossible on the one platform we
started from — which is the worst possible shape for a cross-platform addon**: it would appear to
work here and fail on the other two.

> Note the pattern, because §4 repeats it exactly: *every* in-process web view in Qt demands a call
> before `QApplication` is created, and an addon by construction runs after. This is the structural
> reason the answer is not "find the right wheel".

### 2.4 The AddonManager refuses it by name

Not an inference — it is in the AddonManager shipped in this bundle
(`Mod/AddonManager/addonmanager_dependency_installer.py`, `_install_required`): **[measured]**

```python
for pymod in self.python_requires:
    if pymod.lower().startswith("pyside"):
        continue  # Do not attempt to install PySide, which must be part of FreeCAD already
```

Any `pyside*` entry in an addon's `package.xml` `<depend type="python">` is **silently skipped**
(that is the real tag; there is no `<pythonrequires>` or `<pipdependencies>`, and no version
constraints — [Package Metadata](https://wiki.freecad.org/Package_Metadata)). A second gate applies
independently: `addonmanager_installer.py` loads `ALLOWED_PYTHON_PACKAGES.txt` and then overrides it
with a copy fetched on every run from
`raw.githubusercontent.com/FreeCAD/FreeCAD-addons/master/ALLOWED_PYTHON_PACKAGES.txt`. The 82-name
backup list holds `requests`, `numpy`, `pillow`, `ezdxf`, `ifcopenshell`, `qtpy`, `trimesh` … and
**none** of `pyside6-addons`, `pywebview`, `cefpython3`, `flask`, `aiohttp`, `fastapi`, `uvicorn`,
`websockets`, `qasync`. **[measured]** Anything off it is stripped from `python_requires` and the
user sees an error dialog: *"requires Python packages that are not installed, and cannot be installed
automatically … you must install the following Python packages manually"*
(`addonmanager_installer_gui.py::_handle_disallowed_python`).

So Route A is unshippable through the AddonManager under any packaging — it would be a README asking
users to run pip by hand against the interpreter inside `/Applications/FreeCAD.app`.

### 2.5 The size

Download sizes from the PyPI metadata API for 6.8.3, in MB of 10⁶ bytes:

| wheel | macOS universal2 | Windows amd64 | Linux x86-64 |
|---|---|---|---|
| `PySide6-Addons` | **302.4** | 127.9 | 160.5 |
| `PySide6-Essentials` (forced by the pin) | **134.2** | 72.2 | 95.1 |
| `shiboken6` | 0.4 | 1.2 | 0.2 |
| **total download** | **437** | 201 | 256 |

Unpacked, the macOS Addons tree is **0.73 GB**, of which the `QtWebEngineCore.framework` binary alone
is 383.4 MB. Against a FreeCAD that is 103.8 MB packaged on `osx-arm64` (§1). The addon would be
several times the size of the CAD system it plugs into, to draw a chat panel. There is no arm64-only
wheel on macOS, so Apple-silicon users pay for the Intel slice too.

### 2.6 Code signing — the one objection that does *not* hold

`codesign -dv --entitlements -` on the bundle: `Identifier=org.freecad.FreeCAD`,
`TeamIdentifier=289DJRF23X`, `flags=0x10000(runtime)` — hardened runtime — with entitlements already
including **`com.apple.security.cs.disable-library-validation = true`**. **[measured]** Library
validation is off, so third-party dylibs load. The wheel's `QtWebEngineProcess` helper — a separate
executable at
`PySide6/Qt/lib/QtWebEngineCore.framework/…/Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess`
with its own `.entitlements` **[measured]** — is `CS_ADHOC | CS_LINKER_SIGNED` without hardened
runtime, and launches. The bundle's `site-packages` is writable without `sudo`, and the code
signature seal is already broken by FreeCAD's own `__pycache__`.

Worth stating plainly because it is the objection people expect to be decisive: **it isn't.** Route A
dies on Python import semantics, Qt ABI and Qt's initialisation order, not on Gatekeeper.

### 2.7 The other pip browsers

`cefpython3`: last release **2021-02-16**, Windows-only wheels, Python ≤ 3.9, last macOS wheel 66.0
from 2018 and Intel-only ([PyPI](https://pypi.org/project/cefpython3/)). Dead.
`pywebview`: creates its own Cocoa/WKWebView `NSWindow` — a separate top-level window, **not
dockable**, and its Qt backend is Linux-only ([repo](https://github.com/r0x0r/pywebview)).
`PyQt6-WebEngine`: drags in a *third* Qt (6.11) plus SIP bindings.

**Verdict on Route A: not feasible.** Not expensive — impossible, three times over.

## 3. Route B — a server inside FreeCAD, the React app in the user's browser

No embedded browser at all: FreeCAD serves the built React bundle over loopback HTTP and holds a
WebSocket for the RPC; the user's own browser renders it. This is the only route that works today on
all three platforms with nothing installed, and FreeCAD's own source already contains the pattern.

### 3.1 It works, measured, with zero new dependencies

Running under `/Applications/FreeCAD.app/Contents/Resources/bin/python` with
`QT_QPA_PLATFORM=offscreen` (§8 reproduces it): a `QTcpServer` subclass and a `QWebSocketServer`
both bind loopback on FreeCAD's own Qt event loop, a real socket client gets back
`HTTP/1.1 200 OK` in **2.99 ms**, and the request handler runs on the **main thread**. **[measured]**

```
{"http_listen": true, "ws_listen": true, "handler_thread_is_main": true,
 "http_status": "HTTP/1.1 200 OK", "http_ms": 2.99, "ws_port_open": true}
```

The imports used are `PySide6.QtNetwork` and `PySide6.QtWebSockets`, both present in the bundle.
**[measured]** Nothing is pip-installed, so §2.2's AddonManager gates never engage.

### 3.2 FreeCAD already does exactly this

`Web.so` is in the macOS bundle and exposes `startServer(address=127.0.0.1, port=0)`. **[measured]**
Its implementation is the pattern this route wants, from
[`src/Mod/Web/App/Server.h`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Web/App/Server.h):
`class AppServer : public QTcpServer` with `void incomingConnection(qintptr socket) override`, and a
`ServerEvent : public QEvent` carrying the `QTcpSocket*` so work is delivered back through Qt's event
queue. A loopback socket server living on FreeCAD's Qt event loop is not a workaround; it is the one
piece of the Web module that FreeCAD 1.x kept when it deleted the browser (§1).

### 3.3 How a Qt event loop and an async server coexist — concretely

FreeCAD owns `QApplication::exec()`. An addon is imported into a running loop and can never take it
over. Four candidate designs, and why only one is right:

**(a) Pure Qt, no asyncio — recommended.** `QTcpServer` for the static React bundle,
`QWebSocketServer` (`PySide6.QtWebSockets`) for the RPC channel. Both are `QObject`s living on the
main thread, driven by FreeCAD's own loop; `readyRead` and `textMessageReceived` fire as ordinary
queued slots. There is no second loop to reconcile because there is no second loop. Critically, this
puts handlers **on the main thread**, which is where FreeCAD's document and GUI API must be called
from: Qt states that "the GUI classes, notably `QWidget` and all its subclasses, are not reentrant.
They can only be used from the main thread"
([threads-qobject.html](https://doc.qt.io/qt-6/threads-qobject.html)). So a request can call
`App.ActiveDocument…` and touch the dock directly, with no marshalling. Measured working in §3.1.

**(b) asyncio (aiohttp/uvicorn) on a background thread.** `aiohttp` 3.13.2 is **already in the
bundle** (`conda-meta/aiohttp-3.13.2-py311hee9e2a2_0`) **[measured]**, so this costs 0 MB. It costs
correctness instead: the handler runs on a non-GUI thread, so every FreeCAD call must hop to the main
thread via `QTimer.singleShot(0, fn)` or `QMetaObject.invokeMethod(obj, Qt.QueuedConnection)`, and
every *result* must come back via a `concurrent.futures.Future` the asyncio side awaits. Each RPC
becomes a two-hop round trip, and blocking the asyncio thread on a result while the main thread sits
in a modal dialog — which FreeCAD opens routinely — deadlocks. It buys nothing Route (a) lacks.

**(c) `qasync`.** Its documented entry point is `asyncio.run(main(app), loop_factory=QEventLoop)`
([README](https://github.com/CabbageDevelopment/qasync)) — qasync *replaces* the top-level run and
drives Qt from `QEventLoop.run_forever()`. FreeCAD already called `exec()`; there is no supported way
to attach to a loop someone else is running. It is also not on the AddonManager allow-list (§2.2).
Reject.

**(d) stdlib `http.server` on a thread.** Serves static files adequately and needs nothing installed,
but it gives no push channel — progress from a long agent turn would have to be polled — and it
reintroduces (b)'s thread hop. Use only if Qt were unavailable, which it is not.

Note in passing: `QtHttpServer` and `PySide6.QtAsyncio` would both be pleasant here, and both ship
**only** in `PySide6-Addons` **[measured]** — i.e. behind §2. Route (a) needs neither.

### 3.4 What it costs, and what it buys

Costs, all UX:

- **A second window.** The panel stops being a dock: it cannot be tabbed with Tasks, cannot be split
  beside the 3D view by FreeCAD's own layout, and does not save into FreeCAD's window state.
- **Focus juggling.** Pointing at an edge in the 3D view and typing about it becomes two
  applications' worth of focus changes, and on macOS the browser is a different app in the Dock and
  ⌘-Tab order. For a Copilot whose whole value is standing next to the model, this is the real bill.
- **Lifecycle.** The tab outlives FreeCAD. The WebSocket close event is the only signal that FreeCAD
  went away, so the page must render a disconnected state rather than silently going stale.
- **Ports.** Bind `127.0.0.1` only and take port 0 (kernel-assigned) as `Web.startServer` does, then
  tell the browser the port — a fixed port collides with a second FreeCAD instance.
- **Dev-mode CORS.** A Vite dev server on `:5173` talking to FreeCAD on another port is
  cross-origin; production serves the built bundle from the same origin, so this is a dev-only
  concession, not a shipped one.

Buys:

- Works **today**, on macOS, Windows and Linux, with **0 MB** added and no pip dependency, so it
  passes the AddonManager unchanged.
- The renderer is the user's own up-to-date browser, so no Chromium to ship, patch or CVE-track — and
  devtools are the browser's devtools.
- The process boundary is a socket, which is the same boundary Route D needs. **Route B is a strict
  prefix of Route D**: if a native window is wanted later, it wraps this server and nothing in the
  addon changes.

## 4. Route C — render HTML inside Qt without WebEngine (QtWebView, QtQuick, QTextBrowser)

Dead, for three independent reasons, each citable.

**It is not in the bundle, and the only way to get it is Route A.** `PySide6.QtWebView` raises
`ModuleNotFoundError` in FreeCAD's own interpreter, alongside `QtWebEngineWidgets`,
`QtWebEngineCore` and `QtHttpServer`. **[measured]** The bundle's PySide6 has 39 `Qt*` extension
modules and `QtWebView` is not among them; `find` over the whole `.app` returns exactly one
`WebView` hit, a `.qdocconf` documentation fragment. **[measured]** On PyPI, `QtWebView.abi3.so` and
`QtWebView.framework` ship in `PySide6-Addons` and **not** in `PySide6-Essentials` **[measured]** —
the same 302 MB wheel that carries WebEngine. So "QtWebView instead of WebEngine" is not an
alternative to Route A; it *is* Route A, with §2.1's binary incompatibility unchanged.

**On two of the three platforms it renders with WebEngine anyway.** From
[doc.qt.io/qt-6/qtwebview-index.html](https://doc.qt.io/qt-6/qtwebview-index.html), verbatim:

> On Windows, Qt WebView can use both Qt WebEngine module and WebView2 to render content.
> **On Linux, Qt WebView depends on the Qt WebEngine module to render content.**
> On macOS, the system web view is used in the same manner as iOS.

The native-web-view story is real only on macOS (WKWebView). On Linux it is a thin wrapper over the
exact dependency we were trying to avoid.

**And an addon can never satisfy its precondition.** Same page, Prerequisites:

> To make the Qt WebView module function correctly across all platforms, it's necessary to call
> `QtWebView::initialize()` **before creating the QGuiApplication instance** and before window's
> `QPlatformOpenGLContext` is created.

A FreeCAD addon is imported by a FreeCAD that has already constructed its `QApplication` and its main
window. There is no point in an addon's lifetime at which that call is still possible. The same page
also warns that overlapping the web view with other QML items "is not supported" and gives
"unpredictable results" — which rules out compositing it into the existing dock decoration.

**The remaining in-Qt options are not HTML engines.** `QTextBrowser` implements Qt's rich-text
subset, not a DOM, and no JavaScript at all — React cannot run in it under any configuration.
`QtQuick`'s `WebEngineView` QML type is part of `QtWebEngineQuick`, i.e. WebEngine again.
`QtWebChannel` **is** present in the bundle **[measured]**, but it is a transport — it marshals
`QObject` properties, signals and slots to a JavaScript peer — and it renders nothing; it needs a web
view or a socket on the other end. Which is exactly what Route B uses it for the *idea* of, without
needing the module.

**The macOS-only escape hatch is also closed here.** Embedding a native `WKWebView` into a `QWidget`
via `winId()` → `NSView` would need pyobjc; `import objc` fails in FreeCAD's interpreter — pyobjc is
not in the bundle **[measured]** — and it would be macOS-only, which is the opposite of the
requirement.

## 5. Route D — a separate desktop app (Electron / Tauri / pywebview) over a socket

This is Route B with a native shell around the browser: same server, same protocol, but the UI is a
program we ship rather than a tab the user already has. FreeCAD's own ecosystem has run the
experiment, and the result is specific.

### 5.1 The live precedent says the window will not embed on macOS

**Render** (`FreeCAD/FreeCAD-render`, in the FreeCAD org, 232★, in the current index) does exactly
this. `Render/subcontainer.py`, in its own docstring:

> This module allows to embed an external application in FreeCAD. It is firstly intended to embed
> QtWebEngine features, **following the removal of QtWebEngine from FreeCAD dependencies (May 2024)**.

Its `plugins/README.md` calls them "small programs … run as separate subprocesses in a dedicated
Python virtual environment (fork/exec), while being embedded in FreeCAD Gui", talking over "a
bidirectional socket, based on localhost or named pipes, according to OS". `Render/virtualenv.py`
builds a venv in the user directory and `pip install`s `["PyQt6", "PyQt6-WebEngine", "renderplugin",
"QtPy"]` — it brings its own web engine from PyPI, out of FreeCAD's process, which is the only way
§2 can be made to work at all.

Embedding is `QWindow.fromWinId(winid)` + `QWidget.createWindowContainer(...)`. And on macOS it
crashes FreeCAD: issue
[#480](https://github.com/FreeCAD/FreeCAD-render/issues/480) reports
`libshiboken: Overflow: Value 5032641280 exceeds limits of type [signed] "i" (4 bytes)` on emitting
the window id, with the reporter noting *"The external client, however, starts and is functional."*
The open fix, [PR #484](https://github.com/FreeCAD/FreeCAD-render/pull/484), is titled *"Added MacOS
specific logic to overcome missing embedded window calls"* — i.e. **stop embedding on macOS and let
the plugin be a separate top-level window.**

So Route D does not buy back the dock. On macOS it lands in exactly the place Route B already is — a
second window — having paid for a whole extra application to get there.

### 5.2 Distribution through the AddonManager

The AddonManager installs by `git clone` or by extracting a zip, with no content filter, so a binary
can physically travel in an addon repo. Three things bite anyway:

- **The index Qualities** for listing require an addon to be "Python 3+ based" and to use
  "FreeCAD-provided Qt wrappers", with dependencies drawn from the allow-list — which today holds
  **91 packages** (fetched live from
  `raw.githubusercontent.com/FreeCAD/FreeCAD-addons/master/ALLOWED_PYTHON_PACKAGES.txt`)
  **[measured]** and contains no `flask`, `fastapi`, `uvicorn`, `websockets`, `pywebview` or
  `PyQt6-WebEngine`. `autobahn` is the single web-adjacent entry. `package.xml`'s `<depend>` has no
  binary kind at all.
- **Executables do not survive the transport.** `zipfile.extractall` drops the executable bit
  (0755 → 0644), so a zip-installed addon's binary will not run without a `chmod` at first launch.
- **Unsigned binaries are killed outright.** An unsigned arm64 executable gives `Killed: 9`. The
  mitigating detail is that a payload fetched by `curl`/`urllib` carries no `com.apple.quarantine`
  xattr, so there is no Gatekeeper *prompt* — but the code-signature requirement on arm64 is not a
  policy that quarantine controls, it is enforced on every arm64 binary.

### 5.3 Signing, notarisation, updates

Shipping a Mac app means the Apple Developer Program at **$99/yr**; the free tier cannot notarise.
And macOS 15 Sequoia removed the Control-click bypass, so an unnotarised app now requires the user to
walk into System Settings ▸ Privacy & Security to run it at all. Windows brings its own SmartScreen
reputation problem. Then the update story forks: FreeCAD updates the addon through the AddonManager,
while the desktop app updates itself through some other channel, and the two can disagree about the
protocol version — a failure mode neither Route A nor Route B has, because in both the UI and the
addon are one artefact.

### 5.4 Sizes, and the Linux engine problem

Electron's runtime alone zips at **116.5–118.5 MB** on macOS, 118.2–119.8 MB on Linux and
137.7–140.0 MB on Windows (v43.4.0 release assets). Tauri is the opposite trade: "as little as
600 KB", because it uses the system web view — WebView2 on Windows, WKWebView on macOS, webkit2gtk on
Linux ([webview-versions](https://v2.tauri.app/reference/webview-versions/)). That saving is paid in
engine fragmentation: three different browsers for one React app, and Tauri's own documentation says
of the third *"The diverse nature of the Linux ecosystem means it is very hard to compile accurate
information about WebKitGTK on the various distros."* WebView2 is 0 MB on Windows 10+/11 but needs a
1.8 MB bootstrapper (or a 127 MB offline / 180 MB fixed runtime) otherwise, and Linux needs
`libwebkit2gtk-4.1` present.

There are **zero** FreeCAD Electron or Tauri projects in existence to copy from.

**Verdict on Route D:** technically possible, and the only route that could ever give a *native*
window we control — but on macOS it demonstrably cannot dock, it costs $99/yr plus a second update
channel, and it is strictly more work than Route B for the same second window. Worth revisiting only
if a native window becomes a requirement, and then it wraps Route B's server unchanged.

## 6. Precedent: has any FreeCAD addon ever shipped a web/React UI?

**It has been tried four times. Every one is archived, delisted, degraded to the system browser, or
moved out of FreeCAD's process. No addon in the current official index renders an application UI in
an embedded browser, and none ships React.**

That is from grepping 9 131 `.py`/`.ui`/`.qml` files across *both* official indexes — the legacy
`FreeCAD/FreeCAD-addons` `.gitmodules` (167 addons) and the live
`FreeCAD/Addons` `Data/Index.json` (173 addons, the URL `AddonCatalogCacheCreator.py:48` uses)
**[measured]** — plus an independent language sweep of all 167 GitHub-hosted entries in the live
index, which finds **no React, Vue, Svelte, Vite, webpack or `node_modules` anywhere**; the only
`package.json` in the catalogue is `HistoryWorkbench/docs-static-site/`, a documentation site.
**[measured]** The other web-language hits are Qt stylesheet themes (Dracula, Catppuccin, NordicFC,
OpenTheme), 27 454 static wiki pages in `FreeCAD-Documentation-html`, Doxygen output in `quetzal`,
and a vendored `docutils` tree in `AnimationFreeCAD`.

### The four that really embedded a browser

| addon | mechanism | in the index? | macOS |
|---|---|---|---|
| **ExtMan** (`mnesarco/FreeCAD_ExtMan`) | whole UI is HTML+JS in a `QWebEngineView`, custom `extman://` `QWebEngineUrlSchemeHandler`, `QWebChannel` Python↔JS bridge | **no** — archived 2021, delisted | never; needed distro `python3-pyside2.qtwebenginewidgets` |
| **3DfindIT** (`cadenasgmbh`) | commercial part catalogue in `QWebEngineView` + custom `QWebEnginePage`/`Profile`, JS→Python `native_api` bridge | **no** — README *"no longer maintained"* (2026-05-06), delisted | needs bindings FreeCAD does not ship |
| **Help** (now core `src/Mod/Help/Help.py`) | `openBrowserHTML()` builds a `QWebEngineView` and docks it — **only if** `get_qtwebwidgets()` succeeds | core | **falls back to the system browser** |
| **Render** (`FreeCAD/FreeCAD-render`, 232★) | `QWebEngineView` in `plugins/*/__main__.py` — **separate OS processes** with their own venv | **yes** | web view runs; **window embedding crashes** (§5.1) |

Everything else that "renders HTML" in the corpus is `QTextBrowser.setHtml()` — Qt's rich-text
subset, not a browser: the AddonManager's own README pane
(`Widgets/addonmanager_widget_readme_browser.py`, `class WidgetReadmeBrowser(QtWidgets.QTextBrowser)`),
GDML, kicadStepUp, Ship, Rocket, SearchBar, DesignSPHysics and others.

### The Copilot-shaped population says the same thing

Across the 11 leading FreeCAD AI/MCP repos (651 `.py` files), **not one renders HTML in a web
view** — `ghbalf/freecad-ai` (428★), `robhicks/FreeCAD-Assistant`, `mattyg/journeyman` and the rest
all use `QTextBrowser` + `setHtml`. **Every FreeCAD chat UI that exists today is
`QTextBrowser.setHtml()`.** And GitHub search for `freecad electron`, `freecad tauri`,
`freecad pywebview` returns no FreeCAD project at all.

### Route B, on the other hand, is shipped and indexed

| addon | how |
|---|---|
| **MnesarcoUtils → Remote Control** | `remote/server.py`: `HTTPServer` + `SimpleHTTPRequestHandler` on a `QThread`, doc-root `resources/ui/remote/` (`index.html` + `css/`), port 8521; then a dialog: *"You can connect your device to: http://&lt;lan-ip&gt;:8521"* |
| **Channels** (70★) | *"interconnect FreeCAD with external programs using tcp connections"* — `HTTPServer` + raw sockets, autodiscovery, ships a Blender extension as the external half |
| **SimplyPrint** | `oauth.py` — localhost `HTTPServer` as an OAuth callback plus `webbrowser.open(...)` |
| **core FreeCAD** | `Web.startServer()` (§1); BIM's `importWebGL.py` |

All three addons are in the current index. And the most-starred FreeCAD AI project,
**`neka-nat/freecad-mcp` (1 833★)**, is the reference implementation of the hard part: a
`FilteredXMLRPCServer` bound to `127.0.0.1:9875` in a **daemon thread**, marshalling every call onto
the GUI thread via `rpc_server/gui_dispatch.py` — per-call response queues, a Qt signal to wake the
GUI thread, a `QTimer.singleShot(500, process_gui_tasks)` heartbeat fallback, and a *mouse-button
guard* so an RPC cannot interrupt a 3D navigation drag. **Read `gui_dispatch.py` before writing any
socket server inside FreeCAD** — and note that every one of those four mechanisms exists only because
the server is on a *non-GUI* thread. §3.3(a) deletes all four.

### What FreeCAD's own Help viewer decided

[`src/Mod/Help/Help.py`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Help/Help.py)
probes and degrades — `get_qtwebwidgets()` does `from PySide import QtWebEngineWidgets` inside a
`try`, and on failure logs *"PySide QtWebEngineWidgets module is not available. Help rendering is
done with the system browser"*. The fallback is:

```python
ret = QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))
if not ret:
    import webbrowser
    webbrowser.open_new(url)
```

Given §1 that is not a fallback on FreeCAD 1.x — it is the only live path, on all three platforms.
FreeCAD's own documentation UI renders in the user's system browser, and those four lines are the
tested, in-tree way to open it.

## 7. Comparison

| route | mac | win | linux | via AddonManager | dockable | extra MB | biggest risk |
|---|---|---|---|---|---|---|---|
| **A** pip `PySide6-Addons` into FreeCAD's Python | ✖ | ✖ | ✖ | **no** — installer skips `pyside*` by name | yes, if it worked | **437** dl / 0.73 GB unpacked (mac) | bundled `PySide6` always wins on `sys.path`; forcing it in gives two Qt runtimes and breaks FreeCAD (forum t=106855) |
| **B** loopback server in-process + system browser | ✔ | ✔ | ✔ | **yes**, unchanged | **no** — second window | **0** | UX: a browser window beside the 3D view, focus juggling |
| **C** QtWebView / QtQuick without WebEngine | ✖ | ✖ | ✖ | no | n/a | 437 (same wheel) | `QtWebView::initialize()` must precede `QGuiApplication`; on Linux it needs WebEngine anyway |
| **D** Electron/Tauri/pywebview over a socket | ✔ app, ✖ dock | ✔ | ✔ | binary yes, listing quality no | **no on macOS** (Render #480 crashes FreeCAD) | 0.6 (Tauri) – 140 (Electron) | $99/yr signing + a second update channel, for the same second window as B |
| *(status quo)* `QTextBrowser.setHtml` | ✔ | ✔ | ✔ | yes | **yes** | 0 | no JS, no DOM — not React |

Two columns decide it. **Nothing renders React inside the FreeCAD window on any platform** — the dock
is not available to a web UI at all, so "we lose the dock" is not a cost of Route B, it is the
precondition of every route that renders HTML. And **only Route B costs nothing and ships through the
AddonManager unchanged.**

## Recommendation

1. **Route B.** A `QTcpServer` for the built React bundle and a `QWebSocketServer` for the RPC, both
   on FreeCAD's own Qt event loop — measured working in FreeCAD's interpreter, 2.99 ms, handlers on
   the main thread, **zero new dependencies** (§3.1). Open it with
   `QDesktopServices.openUrl` and a `webbrowser.open_new` backstop, which is what FreeCAD's own Help
   does (§6).
2. **Do not put the server on a thread.** That single choice deletes the response queues, the
   heartbeat timer and the drag guard that `freecad-mcp` needs and the busy-wait `MnesarcoUtils`
   pays (§6). FreeCAD API calls then happen directly, on the thread Qt requires.
3. **Keep the dock.** Ship the React app *alongside* a small Qt panel — the dock stays the place you
   click to open the UI and shows connection state. Today's `QTextBrowser` chat is what every other
   FreeCAD AI addon uses, and it remains the fallback if the browser is closed.
4. **Treat Route D as deferred, not rejected.** It reuses Route B's server verbatim; nothing in the
   addon changes if we later wrap it in Tauri. Do not pay $99/yr and a second update channel for a
   window that still will not dock on macOS.
5. **Route A and Route C are closed.** Not expensive — impossible: `PySide6` is a regular package
   FreeCAD's copy always wins, the AddonManager refuses `pyside*` by name, and every in-process Qt
   web view demands an initialisation call before `QApplication` exists, which an addon runs after.

## 8. Reproducing the measurements

All against `/Applications/FreeCAD.app/Contents/Resources/bin/python` (PySide6 6.8.3 / Qt 6.8.3).

```bash
# which Qt modules exist
cd /Applications/FreeCAD.app/Contents/Resources
PYTHONPATH=$PWD/Ext ./bin/python -c "
for m in ('PySide6.QtWebEngineWidgets','PySide6.QtWebEngineCore','PySide6.QtWebView',
          'PySide6.QtHttpServer','PySide6.QtWebChannel','PySide6.QtWebSockets','objc'):
    try: __import__(m); print('OK  ', m)
    except Exception as e: print('FAIL', m, type(e).__name__)"

# the bundle is conda, and it has no WebEngine
python3 -c "
import json,glob
d=json.load(open(glob.glob('/Applications/FreeCAD.app/Contents/Resources/conda-meta/pyside6-*.json')[0]))
print(d['name'],d['version'],d['build'],d['subdir']); print('depends:',d['depends'])
print('webengine files:',[f for f in d['files'] if 'webengine' in f.lower()])"

# the AddonManager's two gates
AM=/Applications/FreeCAD.app/Contents/Resources/Mod/AddonManager
sed -n '119,135p' $AM/addonmanager_dependency_installer.py     # the pyside skip
curl -s https://raw.githubusercontent.com/FreeCAD/FreeCAD-addons/master/ALLOWED_PYTHON_PACKAGES.txt \
  | grep -vc '^#'                                              # the live allow-list

# code signature and entitlements
codesign -dv --entitlements - /Applications/FreeCAD.app 2>&1 | head -20

# Route B, end to end (the script in §3.1)
QT_QPA_PLATFORM=offscreen ./bin/python qt_server_probe.py
```

`qt_server_probe.py` builds a `QApplication`, subclasses `QTcpServer` to answer one HTTP request,
opens a `QWebSocketServer`, drives a real socket client from a worker thread, and asserts on
`QThread.currentThread()` inside the handler. It prints:

```json
{"http_listen": true, "ws_listen": true, "handler_thread_is_main": true,
 "http_status": "HTTP/1.1 200 OK", "http_ms": 2.99, "ws_port_open": true}
```

Wheel contents were read without downloading, by HTTP `Range` request on the zip central directory of
`PySide6_Addons-6.8.3-cp39-abi3-macosx_12_0_universal2.whl` (302.4 MB, 1596 entries) and of the
`PySide6-Essentials` and `shiboken6` wheels.

## Sources

**FreeCAD source (`raw.githubusercontent.com/FreeCAD/FreeCAD/<ref>/…`)**
- `cMake/FreeCAD_Helpers/SetupQt.cmake` @ `0.21.2` vs `1.0.0` vs `main` — the `WebEngineWidgets` component
- `cMake/FreeCAD_Helpers/InitializeFreeCADBuildOptions.cmake` — `option(BUILD_WEB …)` at all tags
- `src/Gui/CMakeLists.txt` @ `0.21.2` vs `1.0.0` vs `main` — `-DQTWEBENGINE`
- `src/Mod/Web/CMakeLists.txt`, `src/Mod/Web/Init.py`, `src/Mod/Web/InitGui.py`, `src/Mod/Web/Gui/CMakeLists.txt`
- [`src/Mod/Web/App/Server.h`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Web/App/Server.h) — `AppServer : public QTcpServer`
- [`src/Mod/Web/App/AppWeb.cpp`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Web/App/AppWeb.cpp) — `startServer(address=127.0.0.1,port=0)`
- [`src/Mod/Help/Help.py`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Help/Help.py) — `get_qtwebwidgets()`, `show_browser()`
- [`src/App/FreeCADInit.py`](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/App/FreeCADInit.py) — `PathPriority.FallbackLast`
- `src/Mod/Start/CMakeLists.txt` @ `0.21.2` vs `1.0.0`
- commit [`c35693fd`](https://github.com/FreeCAD/FreeCAD/commit/c35693fd) (2024-04-19) *"Web: Remove GUI"*

**FreeCAD wiki**
- [Web Workbench](https://wiki.freecad.org/Web_Workbench) — *"no longer included after version 0.21"*
- [Package Metadata](https://wiki.freecad.org/Package_Metadata) — `<depend type="python">`
- [Addon](https://wiki.freecad.org/Addon)

**AddonManager, as shipped in `/Applications/FreeCAD.app/Contents/Resources/Mod/AddonManager/`**
- `addonmanager_dependency_installer.py` — the `startswith("pyside")` skip; `pip install --target`
- `addonmanager_installer.py` — `_load_local_allowed_packages_list`, `_update_allowed_packages_list`
- `addonmanager_installer_gui.py` — `_handle_disallowed_python`
- `addonmanager_utilities.py` — `get_pip_target_directory`
- `AddonCatalogCacheCreator.py:48` — `ADDON_CATALOG_URL`
- [`ALLOWED_PYTHON_PACKAGES.txt`](https://raw.githubusercontent.com/FreeCAD/FreeCAD-addons/master/ALLOWED_PYTHON_PACKAGES.txt) (live, 91 entries)
- [`Data/Index.json`](https://raw.githubusercontent.com/FreeCAD/Addons/main/Data/Index.json) (173 addons)

**Qt**
- [Qt WebView](https://doc.qt.io/qt-6/qtwebview-index.html) — Linux backend, `initialize()` before `QGuiApplication`
- [Threads and QObjects](https://doc.qt.io/qt-6/threads-qobject.html) — GUI classes "can only be used from the main thread"
- [`qtwebenginequickglobal.cpp`](https://raw.githubusercontent.com/qt/qtwebengine/6.8/src/webenginequick/api/qtwebenginequickglobal.cpp), [`web_engine_context.cpp`](https://raw.githubusercontent.com/qt/qtwebengine/6.8/src/core/web_engine_context.cpp) — `AA_ShareOpenGLContexts`
- [Deploying Qt WebEngine](https://doc.qt.io/qt-6/qtwebengine-deploying.html)
- [qasync](https://github.com/CabbageDevelopment/qasync) — `asyncio.run(main(app), loop_factory=QEventLoop)`

**Packages**
- PyPI JSON API: [PySide6-Addons](https://pypi.org/project/PySide6-Addons/), [PySide6-Essentials](https://pypi.org/project/PySide6-Essentials/), [cefpython3](https://pypi.org/project/cefpython3/)
- anaconda.org API: `conda-forge/pyside6`, `conda-forge/qt6-webengine`, `conda-forge/freecad`

**Addons**
- [Render](https://github.com/FreeCAD/FreeCAD-render) — `subcontainer.py`, `virtualenv.py`, `plugins/README.md`; issue [#480](https://github.com/FreeCAD/FreeCAD-render/issues/480), PR [#484](https://github.com/FreeCAD/FreeCAD-render/pull/484)
- [MnesarcoUtils](https://github.com/mnesarco/FreeCAD_Utils) — `freecad/mnesarco/remote/server.py`, `resources/ui/remote/index.html`
- [Channels](https://github.com/mnesarco/Channels), [SimplyPrint](https://github.com/SimplyPrint/freecad-integration)
- [Ondsel-Lens-Addon](https://github.com/FreeCAD/Ondsel-Lens-Addon), [WebTools](https://github.com/yorikvanhavre/WebTools)
- [ExtMan](https://github.com/mnesarco/FreeCAD_ExtMan), [3DfindIT](https://github.com/cadenasgmbh/3dfindit-freecad-integration)
- [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) — `rpc_server/gui_dispatch.py`

**FreeCAD forum**
- [t=75566](https://forum.freecad.org/viewtopic.php?t=75566) — wmayer on `.abi3.so` vs `.cpython-3XX.so`
- [t=106855](https://forum.freecad.org/viewtopic.php?t=106855) — AddonManager PySide6 update breaks an AppImage
- [t=84500](https://forum.freecad.org/viewtopic.php?t=84500) — chennes: *"I never did get a pip-installed PySide6 to work properly"*
- [t=40349](https://forum.freecad.org/viewtopic.php?t=40349) — `ModuleNotFoundError` after pip

**Other**
- [Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime), [`disable-library-validation`](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.disable-library-validation)
- [Tauri webview versions](https://v2.tauri.app/reference/webview-versions/)
- [pywebview](https://github.com/r0x0r/pywebview)
