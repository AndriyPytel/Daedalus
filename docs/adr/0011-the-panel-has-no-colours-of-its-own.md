# The panel has no colours of its own

The panel was painting itself, and it had no right to. FreeCAD ships its themes as one global stylesheet and never touches `QPalette`, so the palette stays whatever the OS handed over — on macOS with FreeCAD's dark theme active, `Base #ffffff`, `Window #ececec`, `Text #000000`. Every `palette(base)` and `palette(text)` the addon wrote was a request for those light values, painted over a dark theme: a white input card in a dark panel, a white model popup, black glyphs on a dark background.

Better roles would not have fixed it, because there is no role that is right — the whole palette is the wrong palette. So the colours are deleted rather than re-mapped. What is left in the addon's stylesheets is geometry and typography: padding, margin, border-radius, font-size, font-weight. FreeCAD paints the rest, including themes that did not exist when this was written.

Three exceptions, each for a reason that is not style:

- **Semantic colour.** `#4caf50` for success and `#f44336` for failure must *not* follow the theme, and read on both light and dark. `palette(highlight)` stays for the running Step: it is the one role FreeCAD's themes keep meaningful.
- **The icon tint.** An SVG has to be handed a literal colour before it is rasterised. Icons read the foreground of the widget they sit on, after Qt has polished it — a widget carries the global stylesheet's `color` only from that point on, which is what makes it different from `QApplication.palette()`. That is why the tint happens in `showEvent` and not in `__init__`.
- **Translucent grey.** Where something has to separate from its surroundings — the user's turn, a code block, a file chip — the wash is `rgba(127, 127, 127, 40)`. It darkens a light theme and lightens a dark one without asking either what it is.

`tests/test_the_panel_wears_the_users_theme.py` is what keeps this true: it scans every stylesheet string in `ui/` and `Mod/pages/` and fails on a palette role, on a hardcoded colour that is not semantic, and on a rule that pins a dimension without resetting its box.

That last rule is the same defect in the opposite direction. Qt's `min-width`/`max-width` size the *content* rect, so the theme's padding and border are added on top. `#SendButton` asked for 30px and rendered at 36px with a border it never declared; `#HeaderButton`, the one rule that also said `padding: 0px`, was the right size all along.

## Consequences

- **Verification is Level 3.** No automated test can tell us FreeCAD's global stylesheet actually covers a given widget — only that the addon stopped overriding it. The spike #12 asks for (open the panel under a dark theme, look for a widget that falls back to light) has **not** been run; there is no FreeCAD GUI in this environment. Any widget found uncovered gets an explicit exception recorded here.
- **The history popup gave up its drop shadow.** Translucent-plus-shadow needed a background colour of its own, and every such colour is wrong under the theme it was not asked about. Opaque, the theme paints it.
- **`setMaxVisibleItems` needs `QComboBox { combobox-popup: 0; }`** — it is documented to do nothing while the combo has a stylesheet, and this combo has one. Unverified in FreeCAD; if the cap does not hold, the fallback is an explicit `max-height` on the popup's view.
- **The stream lost its boxes.** `AccordionWidget` is a toggle row and a wrapped `QLabel` — not a `QTextEdit` inside the chat's own scroll area, animated to a magic 1000. That nesting is what truncated long thinking and what stole the mouse wheel from the chat. Tool calls and errors now take the same row shape, so there is one collapsible form in the transcript rather than three. The code block and the Plan Gate keep their containers: one must never be mistaken for prose, the other is a question being asked.
