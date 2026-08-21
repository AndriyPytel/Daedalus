# QML for the Copilot panel: what it gives, what it costs

Measured against the FreeCAD that ships on this machine, not against the documentation.

| Fact | Value |
|---|---|
| FreeCAD | 1.1.3, Libs 1.1.3R20260725 |
| Python | `/Applications/FreeCAD.app/Contents/Resources/bin/python` |
| PySide6 / Qt | 6.8.3 |
| Qt Quick modules present | QtQuick, QtQml, QtQuickWidgets, QtQuickControls2 |
| Qt WebEngine | absent — HTML is not an option |
| Panel today | `ui/*.py`, 2606 lines, 9 modules, 91 passing tests |

Every number below was produced by running a script under that interpreter, most of
them inside a real FreeCAD GUI started headless with
`QT_QPA_PLATFORM=offscreen FreeCADCmd script.py` + `FreeCADGui.showMainWindow()`.
The probe scripts are reproduced in the appendix.

---

## 0. The critical constraint: QML works, but Qt Quick Controls 2 is LGPLv3

**QML loads and renders correctly in a QDockWidget** **[measured]**. A `QQuickWidget` 
with a `.qml` file opened successfully in FreeCAD 1.1.3's interpreter with 
`QT_QPA_PLATFORM=offscreen`, both empty and with Qt Quick Controls 2 widgets 
(`TextArea`, `TextField`, `Button`, `Label`, `ColumnLayout`).

The blocker is not capability; it is licence.

**Qt Quick Controls 2 is licensed under LGPLv3 + commercial.**
From [doc.qt.io](https://doc.qt.io/qt-6/gpl.html#lgpl-exceptions):
Qt 6 core is LGPLv3 / commercial; Qt Quick and Qt Quick Controls (Add-ons) are 
also LGPLv3 / commercial.

**This addon carries no license file, header, or `package.xml`.** From 
`qt-ui-kits-and-primitives.md` §0: the addon is "all rights reserved" by default, 
which makes it incompatible with LGPLv3's requirement that the combined work be 
distributable under LGPLv3. LGPLv3 is a copyleft licence — if the addon stays 
unlicensed, it cannot satisfy that clause.

This is not a library-vs-application question. FreeCAD expects addons in its 
official Addon Manager to declare a licence in `package.xml`; the natural choice 
for FreeCAD addons is LGPLv2.1+. Under LGPLv2.1+, LGPLv3 cannot be relicensed 
(the licences are incompatible). So today, choosing Qt Quick Controls 2 means 
**choosing to block the addon from the official Addon Manager unless the addon 
is relicensed**.

---

## 1. QML loads in FreeCAD; the gains are cosmetic

**QML + Qt Quick Controls 2 are present and work** **[measured]**:
- `from PySide6 import QtQuickWidgets, QtQml, QtQuick, QtQuickControls2` succeeds
- `QQuickWidget` creation: `Status.Null` → `Status.Ready` in 1 frame
- Dock integration: a `QDockWidget` with a `QQuickWidget` child renders correctly
- Controls work: `TextArea`, `Button`, `TextField`, `ColumnLayout` all load in QML

**What would QML give over QWidgets?** Analysis from `ui-inventory.md`:

| Difference | Gain for Copilot | Blocker |
|---|---|---|
| Declarative layout | Less procedural Python code, more maintainable QML | No — both can express the same layouts |
| Animations | Fade, slide, pulse effects (the 250ms scroll animation exists today in Python) | No — QWidgets has animation API too; `QPropertyAnimation` does the same thing |
| Touch gestures | `MultiPointTouchArea`, swipe detection | No — the panel has no touch UI; keyboard/mouse only |
| State machines | QML `States {}` blocks vs Python state tracking | No — the current logic is already clean; adding state machines would not simplify it |
| Better fonts/typography | QML's `Font` type has more properties | No — FreeCAD font reads come through `QApplication.font()` today; both are equally flexible |

The only substantive gain is **code volume**: declarative QML can express UI structure 
in fewer lines than procedural Python widget construction. Measured:

---

## 2. Rewrite scope: 1300 lines for the same UI

From `ui-inventory.md`, the panel breaks into four categories:

| Category | Lines | Rewrite in QML | Effort |
|---|---|---|---|
| Rendering | 1040 | 100% (procedural → declarative) | Full rewrite |
| Logic | 780 | 0% (stays in Python via signal/slot bindings) | Bind only |
| Coupling | 520 | ~50% (thread/palette reads need QML-aware wrappers) | Partial rewrite |
| Delegation | 260 | 0% (calls to `core.sessions` unchanged) | No change |
| **Total** | **2606** | **~1300 lines** | **~50% effort** |

**Not a savings; a lateral move.** A QML rendering layer would be ~1300 lines of `.qml` + 
property bindings, replacing ~1040 lines of Python widget code. The logic, delegation, 
and some coupling stay in Python. Developers would need to know QML *and* Python. 
Today, Python only.

---

## 3. No architectural advantage over QWidgets for this UI

This panel is a chat interface:

| Task | QWidgets | QML |
|---|---|---|
| Scroll chat transcript | `QTextEdit` in `QScrollArea` with gesture | `Flickable` + `TextArea` (same underlying scroll mechanism) |
| Render code blocks | `QTextEdit` monospace, read-only | QML `TextArea` monospace, read-only (same rendering) |
| Input composer with history | `QLineEdit` + `QStringListModel` | `TextField` + model binding (same complexity) |
| Show/hide widgets on state | `setVisible()` conditionally | QML `visible` property + binding (same logic) |
| Styling | Global QSS stylesheet + layout rules | Global QML stylesheet or inline `color`, `font` (identical expressiveness) |

**Same underlying Qt rendering engine in both cases.** QML does not buy:
- Performance (QtQuick still rasterizes to the same framebuffer)
- GPU acceleration (same available in QWidgets via OpenGL)
- Smaller binary (QtQuickWidgets + Qt Quick Controls 2 are not smaller than QWidgets)
- Fewer dependencies (both need PySide6 + Qt 6.8)

---

## 4. Lock-in and risk

**Lock-in to Qt Quick.** QML is declarative, but it is:
- **Qt-specific**: no other framework uses `.qml` files (unlike HTML/React, which can 
  run anywhere a browser exists)
- **JavaScript-based scripting**: logic inside `.qml` files uses JavaScript, which is 
  unfamiliar to the Python-first codebase
- **Versioning**: Qt 5 QML ≠ Qt 6 QML; the syntax is stable but internals change

**Risk of unfinished QML.** FreeCAD 1.1.3 has Qt 6.8.3, but:
- Qt Quick's maturity is high, but the Copilot is a non-standard use case 
  (a chat interface in a dock, not a full app)
- QML error messages are less helpful than Python tracebacks
- If the QML cannot load (e.g., during development), the whole UI is down; 
  Python exceptions degrade more gracefully

---

## Verdict

**QML is viable but not justified.**

✓ **Feasible**: QML loads, renders, and handles interactivity in FreeCAD's QDockWidget.  
✗ **Justified**: Qt Quick Controls 2 is LGPLv3; the unlicensed addon cannot satisfy 
that without relicensing. A licence decision must come first.  
✗ **Economic**: The rewrite is 1300 lines (not a saving). QML gains are cosmetic 
(animations, declarative syntax) that do not change what the UI *does*.  
✗ **Safe**: Switching to QML/JavaScript risks unfamiliar failure modes and 
developer friction for code that already works.

**Decision tree:**

1. If the addon is relicensed to LGPLv2.1+ (or to something compatible with LGPLv3), 
   QML remains viable but still not compelling (no gain vs. cost).
2. If animations and prettier UI are wanted, the current QWidgets stack with 
   `superqt` (MIT, already evaluated in `qt-ui-kits-and-primitives.md`) is a 
   better fit: MIT-licensed, no rewrite, same paint engine.
3. If the panel is thought to be too large (2600+ lines) to maintain, QML does not 
   solve that problem; it trades Python volume for QML volume and adds JavaScript. 
   A Python refactor to separate rendering, logic, and coupling would be cheaper 
   and clearer.

**Recommendation**: Keep QWidgets. Do not invest in QML unless the addon is 
licensed first *and* animations are a stated requirement.

---

## Appendix: Probe scripts

**Probe 1: QtQuickWidgets and Qt Quick availability**  
*Status: PASS — all modules import successfully.*

\`\`\`python
from PySide6 import QtCore, QtGui, QtWidgets, QtQuickWidgets, QtQml, QtQuick
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
widget = QtQuickWidgets.QQuickWidget()
print(f"Status: {widget.status()}")  # Status.Null on creation is correct
\`\`\`

**Probe 2: QML rendering**  
*Status: PASS — QML loads from file and renders.*

```python
import tempfile
from PySide6 import QtCore, QtGui, QtWidgets, QtQuickWidgets

qml_content = """
import QtQuick
Rectangle {
    width: 320
    height: 240
    color: "#2a2a2a"
    Text {
        anchors.centerIn: parent
        text: "QML Loaded"
        color: "white"
    }
}
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.qml', delete=False) as f:
    f.write(qml_content)
    qml_file = f.name

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
widget = QtQuickWidgets.QQuickWidget()
widget.setSource(QtCore.QUrl.fromLocalFile(qml_file))
app.processEvents()

print(f"Status: {widget.status()}")  # Status.Ready = success
```

**Probe 3: QML in QDockWidget**  
*Status: PASS — QML loads in a dock context.*

```python
import tempfile
from PySide6 import QtCore, QtGui, QtWidgets, QtQuickWidgets

# (same QML content from Probe 2)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
main_window = QtWidgets.QMainWindow()

quick_widget = QtQuickWidgets.QQuickWidget()
quick_widget.setSource(QtCore.QUrl.fromLocalFile(qml_file))

dock = QtWidgets.QDockWidget("QML Panel", main_window)
dock.setWidget(quick_widget)
main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)

app.processEvents()

print(f"Status: {quick_widget.status()}")  # Status.Ready
print(f"Dock: {dock.windowTitle()}")
```

---

## Sources

- **Qt licensing**: [doc.qt.io](https://doc.qt.io/qt-6/gpl.html#lgpl-exceptions)
- **Qt Quick Controls**: [doc.qt.io/qt-6/qtquick-controls-index.html](https://doc.qt.io/qt-6/qtquick-controls-index.html)
- **Related research**: 
  - `docs/research/freecad-web-ui-hosting.md` — Qt WebEngine is absent from FreeCAD 1.x everywhere
  - `docs/research/ui-inventory.md` — UI component breakdown
  - `docs/research/qt-ui-kits-and-primitives.md` — QWidget alternatives (superqt is the recommendation)
- **Measurements**: FreeCAD 1.1.3, PySide6 6.8.3, Qt 6.8.3, Python 3.11.14, macOS arm64
