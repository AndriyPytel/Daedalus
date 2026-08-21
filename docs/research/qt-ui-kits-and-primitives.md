# Qt UI kits and primitives: what exists for this panel without leaving Qt widgets

Research answering one question: what ready-made material would give the Copilot
panel assistant-ui-class quality **without leaving Qt widgets**, and what does each
option cost in licence, weight and lock-in.

Everything marked *measured* was run in FreeCAD's own interpreter,
`/Applications/FreeCAD.app/Contents/Resources/bin/python` (Python 3.11.14,
PySide6 6.8.3, Qt 6.8.3), with `QT_QPA_PLATFORM=offscreen`, on 2026-08-17.
The probe scripts are reproduced in the appendix so every number can be re-run.

Qt WebEngine is absent from FreeCAD on every platform since 1.0 — confirmed again
here, `import PySide6.QtWebEngineWidgets` raises `ModuleNotFoundError`. HTML/React
inside the dock is impossible and is not considered.

## 0. The licence, which decides the shortlist

**The addon has no licence at all.**

| Looked for | Result |
|---|---|
| `LICENSE` / `COPYING` at repo root | absent |
| `package.xml` (FreeCAD addon metadata, carries `<license>`) | absent |
| `pyproject.toml` / `setup.py` | absent |
| Licence header in `ui/*.py`, `InitGui.py`, `Init.py` | none — no file carries a copyright or licence header |
| Licence section in `README.md` | none |
| Licence file ever committed | none (the only `LICENSE` files in history are vendored `lib/*.dist-info/licenses/`) |

`Init.py` carries the only ownership metadata: `__author__ = "Andrii Pytel"`,
`__url__ = "https://github.com/AndriyPytel/FreeCadCopilot"`.

Two consequences, and they point in opposite directions:

1. **Default copyright is "all rights reserved".** Publishing source without a
   licence grants nobody any rights. That is the most restrictive footing possible,
   and it is almost certainly not what the owner intends for a FreeCAD addon.
2. **It makes the GPL question undecidable rather than answered.** A GPLv3-only
   dependency requires the combined work to be distributed under GPLv3. An
   unlicensed work cannot satisfy that, because it grants no rights to begin with.
   So today a GPLv3-only widget kit is a non-starter — not because the licences
   conflict, but because there is no licence to conflict with.

**This must be decided before any dependency is chosen.** The natural choice is
LGPLv2.1+ — it is what FreeCAD itself uses, and it is what the FreeCAD Addon
Manager's official addon list expects addons to declare in `package.xml`. Under
LGPL-2.1+, a GPLv3-only kit is still excluded (GPLv3 cannot be relicensed into
LGPL-2.1), while MIT/BSD/Apache-2.0 kits are all compatible.

The filter applied to every candidate below is therefore: **MIT / BSD / Apache-2.0
is usable; GPLv3-only is not; LGPL is usable only as a separate, unmodified,
dynamically-imported library.**

## 1. Qt widget component kits usable from PySide6 6.8.3

All versions and licences below are read from the PyPI JSON API on 2026-08-17
(`https://pypi.org/pypi/<name>/json`), not from prose.

| Kit | Latest | Licence | Wheel | Deps | Ships a global QSS? | Fights ADR-0011? |
|---|---|---|---|---|---|---|
| PyQt-Fluent-Widgets | 1.11.3 (2026-08-01) | **GPLv3** or commercial | 1.76 MB | — | Yes, its own theme engine | **Yes — and licence-blocked** |
| PySide6-Fluent-Widgets | 1.11.3 (2026-08-01) | **GPLv3** or commercial | 1.55 MB | `PySide6`, `PySideSix-Frameless-Window`, `darkdetect` | Yes | **Yes — and licence-blocked** |
| qt-material | 2.17 (2025-04-21) | BSD-2-Clause | 1.69 MB | `Jinja2` | Yes — 37.5 KB QSS over 44 widget classes incl. `QWidget`, `QMainWindow` | **Yes, fatally** |
| QDarkStyleSheet | 3.2.3 (2023-11-28) | MIT | 0.87 MB | `qtpy>=2` | Yes — that *is* the package | **Yes, fatally** |
| **superqt** | **0.8.2 (2026-05-18)** | **BSD-3-Clause** | **0.10 MB** | `qtpy`, `pygments`, `typing-extensions` | **No** | **No** |
| QtAwesome | 1.4.2 (2026-04-10) | MIT | 2.59 MB | `qtpy` | No — icon fonts only | No (but see below) |
| pyqtgraph | 0.14.0 (2025-11-16) | MIT | 1.92 MB | `numpy`, `colorama` | No | No — but irrelevant here |

### The three that are excluded outright

**QFluentWidgets is licence-blocked, not merely inconvenient.** Both the PyQt and
PySide6 builds carry the PyPI classifier
`License :: OSI Approved :: GNU General Public License v3 (GPLv3)`. Its community
edition is GPLv3 with a paid commercial exception. With the addon carrying **no
licence**, GPLv3 cannot be satisfied; if the addon becomes LGPLv2.1+ to match
FreeCAD, GPLv3 still cannot be relicensed into it. It is also the worst fit
architecturally: it replaces Qt's painting wholesale with a Fluent theme engine,
which is the exact opposite of "the panel has no colours of its own".
`PySide6-Fluent-Widgets` additionally declares `PySide6` as a hard dependency, so
installing it would drop a **second PySide6** into
`AdditionalPythonPackages` alongside FreeCAD's bundled 6.8.3 — an import-order
hazard for the whole application, not just the addon.

**qt-material and QDarkStyleSheet are theme engines.** Their entire purpose is to
call `QApplication.setStyleSheet(...)`. Measured in FreeCAD's Python: `setStyleSheet`
**replaces, it does not merge** — after a kit installs its sheet, the host theme is
gone from `app.styleSheet()` entirely. Applying either one repaints all of FreeCAD,
not the dock. Appending their QSS to FreeCAD's instead of replacing it keeps both
strings but still repaints every `QLabel` and `QPushButton` in the application.
They can only be contained by scoping to `dock.setStyleSheet(...)`, which neither
library offers as an API. QDarkStyleSheet's last release was 2023-11-28.

### The one that survives

**superqt (BSD-3-Clause, 0.10 MB, pure Python, released 2026-05-18)** is the only
kit on the list that is a *component library* rather than a *theme*. It ships no
global stylesheet. Its `pyside6` extra pins `>=6.7.0` on Python 3.12+ with no upper
bound, so 6.8.3 is in range. Verified from the 0.8.2 wheel's own `__all__`:

| superqt component | Replaces, in this repo |
|---|---|
| `QCollapsible` | `ui/accordion_widget.py` — the hand-rolled toggle row |
| `superqt.utils.CodeSyntaxHighlight` | the unhighlighted `QTextEdit` in `CodeBlockWidget` |
| `QSignalThrottler` / `qthrottled` | throttling streamed tokens, currently unthrottled |
| `QFlowLayout` | the attachment chip bar's wrapping |
| `QSearchableListWidget` | the history popup's filter |
| `QElidingLabel` | long titles in the header and history rows |

One ADR-0011 caveat, read out of the 0.8.2 source: `CodeSyntaxHighlight.setTheme`
sets `QPalette.Base` on its parent **only when constructed with a widget**. Passing
`text_edit.document()` instead leaves the palette untouched and applies token
formats only — so the code block keeps the FreeCAD theme's background. That is the
form to use, and ADR-0011 already grants code its own container.

The cost is `qtpy` (MIT), a binding-shim `superqt` imports instead of PySide6
directly. It resolves to PySide6 when PySide6 is already imported, which FreeCAD
guarantees.

**QtAwesome** is a real option for icons but competes with something the repo
already has: `UIHelpers.ICONS` is an inline SVG map tinted from the polished
widget's `foregroundRole`. QtAwesome's `qta.icon('fa5s.stop', color=...)` needs the
same colour handed to it, so it swaps a maintained SVG map for a 2.59 MB font
bundle without solving the colour problem. Not recommended.

**pyqtgraph** is a plotting library. Nothing in this panel plots. Excluded.

### Could any of these ship through the FreeCAD Addon Manager?

The Addon Manager cannot reliably install arbitrary pip dependencies — but this
addon does not rely on it to. `core/dependency_manager.py` already installs
`litellm`, `numpy`, `Pillow`, `pymupdf` and `PyYAML` into
`FreeCAD.getUserAppDataDir()/AdditionalPythonPackages` with `pip install --target`
and prepends that directory to `sys.path`. Adding `superqt` is one more entry in
`REQUIRED_PACKAGES`, subject to the same `--target` fragility that module's own
docstring documents. Verified in FreeCAD's Python: **none of `superqt`, `qtawesome`,
`qdarkstyle`, `qt_material`, `qfluentwidgets`, `pygments`, `markdown_it` or
`mistune` is importable today**; `docutils` 0.22.3, `PIL` 12.0.0 and `numpy` 1.26.4
are.

A 0.10 MB pure-Python BSD package is about the cheapest possible addition. But it is
still a runtime install that can fail on a user's machine, and every widget taken
from it is a widget that fails to import if it does. The alternative — vendoring the
handful of files actually used, which BSD-3 permits with attribution — removes that
failure mode entirely and is worth considering for `QCollapsible` alone.

## 2. What Qt already gives that this addon writes by hand

This is the cheapest win available and the numbers below are measured, not
estimated. Every figure comes from the probe scripts in the appendix, run in
FreeCAD's Python 3.11.14 / PySide6 6.8.3 with `QT_QPA_PLATFORM=offscreen`.

### 2.1 Markdown: `setMarkdown` renders far more than the panel shows

The assistant's message today is a `QLabel` in `ui/chat_widgets.py`:
`AssistantMessageWidget.append_text` does `setText(self.text_label.text() + text)`.
**No markdown is rendered at all.** Bold, lists, tables and fenced code arrive as
literal asterisks, hyphens, pipes and backticks.

Feeding one document through `QTextDocument.setMarkdown` and inspecting the result:

| Markdown feature | Rendered by Qt 6.8.3? | Evidence |
|---|---|---|
| Headings, bold, italic, inline code, strikethrough | Yes | round-trips through `toMarkdown` |
| Bulleted / ordered / nested lists | Yes | 3 `QTextList` objects |
| **GitHub tables** | **Yes** | 1 `QTextTable` in the document's frame tree |
| Task lists `- [ ]` / `- [x]` | Yes, as text | survives round-trip |
| **Fenced code with a language tag** | **Yes, and self-identifying** | block carries `QTextFormat.BlockCodeLanguage == 'python'` (enum 4240), and `BlockCodeFence` (4241) carries the fence character |
| Block quotes, horizontal rules, autolinks | Yes | — |
| Images | Parsed | 1 image fragment produced |

The default dialect is `MarkdownDialectGitHub`. That fenced-code block property is
the important one: **a `QSyntaxHighlighter` can locate code blocks inside a rendered
markdown document without parsing anything.** Verified — a highlighter reading
`BlockCodeLanguage` coloured 4 keywords inside the fence and correctly skipped the
word `def` in the surrounding prose.

One gotcha, measured: a highlighter attached *before* `setMarkdown` sees a single
empty block and highlights nothing; attached *after*, it fires not at all. **An
explicit `rehighlight()` after `setMarkdown` is required** — then it works.

### 2.2 Streaming: `setMarkdown` cannot stream, `insertMarkdown` can

This is the single most important measured fact in this report.

With a `QTextBrowser` shown and layout forced, streaming a message in chunks:

| Chunks (→ chars) | `setMarkdown` each chunk | worst call | `insertMarkdown` append | worst call |
|---|---|---|---|---|
| 100 (4.9 KB) | 279 ms | 13.8 ms | 204 ms | 5.1 ms |
| 200 (9.7 KB) | 946 ms | 8.5 ms | 650 ms | 6.1 ms |
| 400 (19 KB) | 3 175 ms | 14.6 ms | 2 061 ms | 10.1 ms |
| 800 (39 KB) | 11 385 ms | 27.3 ms | 6 350 ms | 15.0 ms |

Cost is not the disqualifier — **scroll behaviour is**:

| Operation | Scroll position | Result |
|---|---|---|
| `setMarkdown` while user scrolled to the middle | 692 → **0** | **RESET** |
| `setMarkdown` while pinned to the bottom | pinned → **0** | **falls to the top** |
| `QTextCursor.insertText` append | 692 → 692 | preserved |
| **`QTextCursor.insertMarkdown` append** | **482 → 482** | **preserved** |
| `setMarkdown` with manual save/restore of `verticalScrollBar().value()` | 482 → 482 | preserved |

`setMarkdown` replaces the whole document, so the transcript jumps to the top on
every token. `QTextCursor.insertMarkdown` exists in PySide6 6.8.3, appends in place,
preserves scroll, renders tables, and preserves `BlockCodeLanguage`.

**But it must be fed complete blocks.** Measured: appending the three tokens
`"Hello "`, `"there "`, `"friend"` yields `'Hellotherefriend'` — markdown strips
trailing whitespace. A bold span split across two chunks (`"say **bo"` + `"ld** now"`)
renders as the literal text `say **bold** now` with no bold. The same string
inserted as one block renders correctly with `bold` emboldened.

The working recipe, verified end to end: keep committed blocks in the document, hold
the in-flight block in a plain-text tail, re-render the tail after each chunk from
one remembered position, and commit with `insertMarkdown` when the block closes.
Streaming a realistic assistant message with a list, a table and a code fence this
way took **17.2 ms for 29 chunks (0.59 ms/chunk)**, produced the table, 3 bold runs,
and left no literal `**` or `|` in the text.

The remaining hand-work is the block splitter: splitting on a blank line is wrong,
because a fenced code block may contain blank lines. Measured — the naive splitter
tears one fence into two and loses the blank line inside it. A fence-aware splitter
(track whether an odd number of ``` has been seen) keeps it. **That splitter is the
one piece of streaming-markdown machinery Qt does not provide.**

### 2.3 The transcript is not virtualised

`CopilotPanel.add_chat_widget` inserts a live `QWidget` into a `QVBoxLayout` before a
trailing stretch. Every message ever rendered stays a widget.

| Messages | `QLabel` in `QVBoxLayout` (today) | live `QWidget`s | `QListView` + `QStyledItemDelegate` |
|---|---|---|---|
| 50 | 100 ms | 50 | 4.5 ms |
| 200 | 69 ms | 200 | 4.1 ms |
| 500 | 190 ms | 500 | 5.0 ms |
| 1000 | 442 ms | 1000 | 6.8 ms |

And rich text does **not** have to be given up to get virtualisation. A
`QStyledItemDelegate` that paints a `QTextDocument` through
`documentLayout().draw(painter, ctx)` renders markdown per row:

- 2000 markdown rows inserted in **206 ms**
- **26** `paint()` calls — only the visible rows
- **5** live `QWidget`s in the whole view

Critically for ADR-0011, the delegate passes `ctx.palette = option.palette`, so the
text is painted in the colour the *view* was polished with — the theme's colour, by
the same mechanism `UIHelpers.get_icon` already relies on. No colour is declared.

### 2.4 The rest of the primitives

| Hand-written today | Qt primitive | Verified |
|---|---|---|
| `AccordionWidget` show/hide with `setVisible` | `QPropertyAnimation` on `b"maximumHeight"` with `QEasingCurve.InOutCubic` | animation ran 0 → 200 and reported 200 on `finished` |
| `DocumentLinkWidget` — a `QLabel` holding an `<a href>` | `QTextBrowser` + `anchorClicked`, with `setOpenLinks(False)` | all three link forms parsed out of markdown, including a **custom `freecad://object/Box` scheme** delivered intact to the slot — the hook for "click an object name, select it in the tree" |
| no completion in the composer | `QCompleter` with `setWidget` on the `QTextEdit` | `'/p'` → `['/plan']`, `'cyl'` → `['Cylinder']` |
| no tooltips on truncated text | `QToolTip.showText` | present |
| unthrottled token repaints | `superqt.qthrottled`, or a `QTimer` coalescing chunks | — |

### 2.5 What could be deleted today, ranked

1. **The plain-text assistant message.** `AssistantMessageWidget` + its O(n²)
   `setText(text() + chunk)` concatenation, replaced by a `QTextBrowser` fed with
   `insertMarkdown`. Deletes hand-written text accumulation *and* adds bold, lists,
   tables and code fences that the panel cannot render at all today.
2. **`CodeBlockWidget`'s unhighlighted `QTextEdit`.** Fenced code inside an assistant
   message is already identified by `BlockCodeLanguage`; a `QSyntaxHighlighter`
   (hand-rolled, or `superqt.CodeSyntaxHighlight` over `.document()`) highlights it.
   The separate widget stops being needed for code the model writes in prose.
3. **`ui/accordion_widget.py`'s toggle mechanics** — `superqt.QCollapsible`, or 15
   lines of `QPropertyAnimation` on `maximumHeight`. The pulsing title is already a
   `QGraphicsOpacityEffect` and is worth keeping.
4. **`DocumentLinkWidget`** entirely — a markdown link in the assistant message plus
   one `anchorClicked` handler covers it, and generalises to in-app actions.
5. **The unvirtualised transcript** — `QVBoxLayout` of widgets → `QListView` +
   `QAbstractListModel` + a markdown-painting delegate. This is the largest change
   and the only one that is a rewrite rather than a substitution.

### 2.6 What this costs the 91 tests

The three UI suites pass today: **91 passed in 15.7 s**
(`.venv/bin/python -m pytest`).

They are far less coupled to the widget classes than their size suggests. Every
assertion about transcript *content* funnels through one helper in
`tests/test_panel_level2.py`:

```python
return " ".join(label.text()
                for widget in widgets
                for label in widget.findChildren(QtWidgets.QLabel))
```

Changing `AssistantMessageWidget` from a `QLabel` to a `QTextBrowser` requires that
helper to also collect `QTextBrowser.toPlainText()` — **one helper, not 91 tests**.
Beyond it, the direct bindings to widget internals are: `link.text_label.text()` and
`link.text_label.openExternalLinks()` (2 assertions, `DocumentLinkWidget`), and
`row.content_area.isVisible()` / `toggle_button.text()` (4 assertions,
`AccordionWidget`). Everything else asserts on `status_line`, `user_input`,
`title_label` and button labels, none of which a transcript change touches.

A move to `QListView` + delegate is different in kind: `findChildren(QLabel)` returns
nothing when messages are painted rather than constructed, so the helper would have
to read the model instead of the widget tree. That is still one helper — but it is
the difference between adapting the suite and rewriting what it observes.

## 3. Chat-specific prior art in the Qt/Python world

**There is no pip-installable, reusable Qt chat-transcript widget.** Nothing on PyPI
provides what this panel needs as a component. That is the honest answer to the
category, and it is a finding rather than a gap in the search.

What does exist is prior art to *read*, in three tiers.

| Project | What it is | Qt widgets? | Licence | Alive | Liftable? |
|---|---|---|---|---|---|
| **KDE `ktextaddons` / `textautogeneratetext`** | A mature LLM chat library — chat model, `QListView` transcript, `QStyledItemDelegate`, markdown block manager, syntax highlighting, attachment chips, history popup, typing indicator, LRU cache of rendered documents, MCP support | **Yes** | **GPL-2.0-or-later** | Yes, actively maintained | **No** — C++, GPL, welded to KDE Frameworks (`KIconLoader`, `KLocalizedString`, `KMessageBox`). **Blueprint only** |
| **`ghbalf/freecad-ai`** | An AI assistant workbench *for FreeCAD*. `chat_widget.py` is 2 940 lines; `message_view.py` is 612 | Yes, PySide | **LGPL-2.1** | Yes | `chat_widget.py` no — welded to its own core. **`message_view.py` genuinely liftable** with light stubbing |
| **`superqt`** | Component library, not a chat client | Yes | BSD-3 | Yes (2026-05) | **Yes** — see §1 |
| qtconsole (Jupyter) | A rich streaming transcript in a `QTextEdit` subclass | Yes | BSD-3 | Yes | Patterns only — welded to the Jupyter kernel protocol |
| `pyqt-openai` / VividNode | PySide6 desktop LLM client | Yes | MIT | Yes | Patterns only — app-coupled |
| qTox `src/chatlog` | The classic Qt chat transcript: custom `QGraphicsScene` with `ChatLine`/`ChatLineContent` | Yes | GPL-3.0 | **Archived** (last push 2025-02) | No — GPL and archived |
| Quassel `src/qtui/chatview` | IRC transcript: `ChatView` + `ChatLineModel` + `ChatLineModelItem` | Yes | GPL-2 (`NOASSERTION` on GitHub) | Yes | No — GPL, C++ |
| GPT4All | Desktop LLM client | **No — QML/Qt Quick** | MIT | Discontinued | No |
| LM Studio, Msty | Desktop LLM clients | **Not Qt — Electron**, closed source | proprietary | Yes | No |
| Jan.ai | Desktop LLM client | **Not Qt — Tauri/TypeScript** | AGPL-3.0 | Yes | No |
| `KercyDing/QMarkdownWidget` | Markdown + LaTeX rendering widgets | Yes | MIT | 1 star, June 2025 | Too immature to depend on |
| `hansonmcoombs/quick-qt-chat-interface` | "a Quick chat bubble interface written in PyQt6… because it seemed dumb that I couldn't steal one" | Yes | MIT | 0 stars, 2024 | Confirms the gap more than it fills it |

Three things follow.

**The architecture is settled, even though the code is not reusable.** KDE's
`textautogeneratetext` and Quassel's `ChatView` — the two most mature Qt chat
transcripts in existence, written twenty years apart — independently arrive at
`QAbstractListModel` + `QListView` + `QStyledItemDelegate` painting a
`QTextDocument`, with a cache of rendered documents. That is the same structure
§2.3 measured at 2000 rows and 5 live widgets. This design is not speculative.

**`freecad-ai` is the most useful thing found, partly as a warning.** It is the same
addon in the same host solving the same problem. Its choices: a hand-rolled regex
markdown-to-HTML converter (`CODE_BLOCK_RE`, `BOLD_RE`, `ITALIC_RE`, an
`UNCLOSED_BLOCK_RE` for truncated streamed fences) fed into a `QTextBrowser`, and a
hardcoded `_LIGHT_THEME_COLORS` / `_DARK_THEME_COLORS` dict of some forty hex
values. **That colour dict is exactly what ADR-0011 exists to forbid**, and it is
what this repo would have to maintain against every FreeCAD theme if it went the
same way. Its `UNCLOSED_BLOCK_RE`, on the other hand, is the same problem §2.2 hit
with fence-aware splitting — independent confirmation that the unclosed streamed
fence is the real hazard, and that regexes are how people lose to it.

**The one genuine component is `superqt.CodeSyntaxHighlight`** — a Pygments-to-
`QSyntaxHighlighter` bridge, BSD-3, pip-installable. It settles the code-highlighting
category outright.

## 4. The assistant-ui feature list against Qt

One row per thing assistant-ui gives free. "Cheap" means a Qt primitive does the
work and the panel writes glue; "hand-work" means Qt gives structure but no
behaviour.

| assistant-ui gives | Qt equivalent | Verdict | Cost |
|---|---|---|---|
| **Streaming markdown** | `QTextCursor.insertMarkdown` — appends in place, preserves scroll, renders tables and fenced code (measured 0.59 ms/chunk) | **Cheap primitive, one missing piece** | Glue + a fence-aware block splitter. Qt has no streaming markdown parser |
| **Reasoning accordion** | `superqt.QCollapsible` (BSD-3), or `QPropertyAnimation` on `maximumHeight` | **Ready-made** | Near zero — the repo's version already works; this deletes its mechanics |
| **Approval gate** | `QFrame` + `QPushButton`, already built as `#GateCard` | **Hand-work, already done** | Nothing to gain. The panel's four question cards are the right shape already |
| **Branching / edit / regenerate** | **Nothing.** No Qt primitive models a conversation tree, an editable past turn, or a re-run from a midpoint | **Hand-work, all of it** | The largest genuine gap. Model layer *and* UI |
| **Virtualised list** | `QListView` + `QAbstractListModel` + `QStyledItemDelegate` painting a `QTextDocument` (measured: 2000 rows, 26 paints, 5 widgets) | **Ready-made and better** | Real: a rewrite of the transcript, and the tests' `findChildren(QLabel)` helper stops working |
| **Attachments** | `QFlowLayout` (superqt) for chip wrapping; drag/drop and clipboard images are `QMimeData`, already handled in `_PasteAwareTextEdit` | **Cheap** | Mostly already built |
| **Code blocks** | `BlockCodeLanguage` identifies fences inside rendered markdown for free; `superqt.CodeSyntaxHighlight` (Pygments) colours them | **Ready-made** | Low — remember `rehighlight()`, and pass `.document()` not the widget |
| **Generative-UI tool rendering** | **Nothing.** No Qt mechanism maps a tool call to a registered custom renderer | **Hand-work** | A registry of tool-name → widget factory. The panel's plan/checklist widget already *is* one such renderer, hardcoded |
| *(Also free in assistant-ui)* auto-scroll pinning | `verticalScrollBar().value() >= maximum() - ε` before insert, restore after | **Cheap** | Three lines, verified |
| *(Also free)* token throttling | `superqt.qthrottled` / `QSignalThrottler`, or a coalescing `QTimer` | **Ready-made** | Near zero |
| *(Also free)* link/citation handling | `QTextBrowser.anchorClicked` with `setOpenLinks(False)`; **custom schemes work** — `freecad://object/Box` arrives intact | **Ready-made and better than the web version** | Low, and it unlocks in-app actions from prose |

Counting the rows: **three are ready-made** (accordion, code blocks, virtualised
list), **four are cheap glue over a real primitive** (streaming markdown,
attachments, auto-scroll, links), and **two are hand-work no matter what**
(branching/regenerate, generative-UI tool rendering).

## 5. Verdict

1. **A "less blood" path exists, and it is not a kit — it is Qt's own text stack.**
   `QTextCursor.insertMarkdown` + `BlockCodeLanguage` + `QSyntaxHighlighter` +
   `anchorClicked` give streaming markdown, tables, highlighted code and clickable
   in-app links, all of which the panel currently cannot render at all.
2. **One dependency is worth taking: `superqt`** — BSD-3, 0.10 MB, pure Python, no
   global stylesheet, giving `QCollapsible`, `CodeSyntaxHighlight`, `qthrottled`,
   `QFlowLayout` and `QSearchableListWidget`. Every other kit either fights
   ADR-0011 by design (qt-material, QDarkStyleSheet) or is licence-blocked
   (QFluentWidgets, GPLv3).
3. **Do the substitutions before the rewrite.** Replacing the plain-text `QLabel`
   assistant message with an `insertMarkdown`-fed `QTextBrowser` is the single
   largest visible quality jump available, and it costs one test helper.
4. **The transcript rewrite to `QListView` + delegate is real but deferrable** —
   measured 65× faster at 1000 messages with 5 live widgets instead of 1000, and
   ADR-0011-safe via `ctx.palette = option.palette`. It is the one change that
   invalidates how the tests observe the panel.
5. **Settle the licence first.** The addon has none; LGPLv2.1+ matches FreeCAD and
   the Addon Manager's expectations, and keeps every MIT/BSD candidate available.
6. **What Qt will never give that assistant-ui would have: branching, editing a
   past turn and regenerating from a midpoint** — the conversation-as-a-tree model.
   No Qt primitive touches it, no Qt chat prior art implements it, and it is
   hand-work in both the model layer and the UI. Generative-UI tool rendering is the
   same story one tier down.

## Sources

Verified in FreeCAD's interpreter on 2026-08-17 — Python 3.11.14, PySide6 6.8.3,
Qt 6.8.3, `QT_QPA_PLATFORM=offscreen`:

- `QTextDocument.setMarkdown`, `QTextCursor.insertMarkdown`, `QTextFormat.BlockCodeLanguage`
  (4240) / `BlockCodeFence` (4241), `QSyntaxHighlighter.rehighlight`, `QListView` +
  `QStyledItemDelegate` + `QAbstractTextDocumentLayout.draw`, `QPropertyAnimation`,
  `QCompleter`, `QToolTip`, `QTextBrowser.anchorClicked`, `QApplication.setStyleSheet`
  replacement semantics, `QtCore.qRegisterResourceData` presence.
- `PySide6.QtWebEngineWidgets` — `ModuleNotFoundError`. `QtCharts`, `QtMultimedia`,
  `QtStateMachine`, `QtPdf` — absent. `QtQuick`, `QtQml`, `QtQuickWidgets`,
  `QtQuickControls2`, `QtSvg`, `QtWebChannel`, `QtWebSockets` — present.
- Importable in FreeCAD today: `docutils` 0.22.3, `PIL` 12.0.0, `numpy` 1.26.4.
  Not importable: `superqt`, `qtawesome`, `qdarkstyle`, `qt_material`,
  `qfluentwidgets`, `pygments`, `markdown_it`, `mistune`.

Package facts from the PyPI JSON API, 2026-08-17:

- <https://pypi.org/pypi/PyQt-Fluent-Widgets/json> — 1.11.3, GPLv3, 1.76 MB
- <https://pypi.org/pypi/PySide6-Fluent-Widgets/json> — 1.11.3, GPLv3, 1.55 MB
- <https://pypi.org/pypi/qt-material/json> — 2.17, BSD-2-Clause, 1.69 MB
- <https://pypi.org/pypi/qdarkstyle/json> — 3.2.3, MIT, 0.87 MB
- <https://pypi.org/pypi/superqt/json> — 0.8.2, BSD-3-Clause, 0.10 MB
- <https://pypi.org/pypi/QtAwesome/json> — 1.4.2, MIT, 2.59 MB
- <https://pypi.org/pypi/pyqtgraph/json> — 0.14.0, MIT, 1.92 MB

superqt exports read from the `superqt-0.8.2-py3-none-any.whl` `__all__` and from
`superqt/utils/_code_syntax_highlight.py` in that wheel.

Projects:

- <https://github.com/pyapp-kit/superqt>
- <https://invent.kde.org/libraries/ktextaddons> — `textautogeneratetext`, GPL-2.0-or-later
- <https://github.com/ghbalf/freecad-ai> — LGPL-2.1
- <https://github.com/qTox/qTox> — GPL-3.0, archived 2025-02
- <https://github.com/quassel/quassel>
- <https://github.com/KercyDing/QMarkdownWidget> — MIT
- <https://github.com/ColinDuquesnoy/QDarkStyleSheet>
- <https://wiki.freecad.org/Package_Metadata> — the `<license>` element addons declare

## Appendix: reproducing the measurements

Every number is produced by a script run as:

```bash
QT_QPA_PLATFORM=offscreen \
  /Applications/FreeCAD.app/Contents/Resources/bin/python <probe>.py
```

The probes, by section:

| Section | What it measures |
|---|---|
| §2.1 | `setMarkdown` a document exercising every feature, then count `QTextTable` / `QTextList` / image fragments and read `BlockCodeLanguage` off each block |
| §2.1 | attach a `QSyntaxHighlighter` before, after, and with an explicit `rehighlight()`; count keywords formatted inside vs outside the fence |
| §2.2 | stream N chunks into a shown `QTextBrowser`, forcing layout with `documentLayout().documentSize()` and `processEvents()` each chunk; time total and worst call |
| §2.2 | scroll to `maximum()//2`, apply each append strategy, compare `verticalScrollBar().value()` before and after |
| §2.2 | append split tokens (`"say **bo"` + `"ld** now"`) and inspect `charFormat().fontWeight()` per fragment |
| §2.3 | insert N messages as `QLabel`s in a `QVBoxLayout` vs rows in a `QListView`; time both and count `findChildren(QWidget)` |
| §2.3 | a `QStyledItemDelegate` painting a `QTextDocument`; count `paint()` calls against 2000 rows |
| §1 | `app.setStyleSheet(host)` then `app.setStyleSheet(kit)`; compare `app.styleSheet()` against both, and `app.palette()` against the polished widget's `foregroundRole` |

The palette result underlying ADR-0011 reproduces exactly: with a global stylesheet
installed, `QApplication.palette()` reports `#000000` while the polished widget's
`foregroundRole` reports the stylesheet's `#dddddd`.
