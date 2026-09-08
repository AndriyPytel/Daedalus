"""The addon has no colours of its own (#12).FreeCAD ships its themes as one global stylesheet and never touches QPalette,so the palette stays whatever the OS handed over — light values on macOS, undera dark theme. Every `palette(base)` the addon wrote was a request for thoselight values, painted over a dark panel. The fix is deletion, not better roles,and deletion is exactly the kind of thing that creeps back one rule at a time.Hence a test rather than a review note.Qt behaviour itself stays Level 3 — a manual script. What is checked here iswhat the source says, which is where this defect lives.ALLOWED_ROLES are the roles FreeCAD's themes do keep meaningful. `highlight` isthe Step that is running and the fill of the one accent control on screen;`highlighted-text` cannot be separated from it, since a glyph left at the theme'sordinary foreground on top of that fill is the pair a theme guarantees isreadable being broken by hand.SEMANTIC holds colour that carries meaning rather than style. These must notfollow the theme (a failed Step is red in every theme), so they stay explicitvalues.Sub-control rules (`::handle`, `::drop-down`) are drawn by the style, not boxedby the theme's widget padding, so the reset does not apply to them. A zero is not a pin either: `min-width: 0px` gives the box back to the widget's own content rather than sizing it, so there is nothing for the theme's padding to be added to."""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
STYLED = [os.path.join(SRC, "ui"), os.path.join(SRC, "Mod", "pages")]

ALLOWED_ROLES = {"palette(highlight)", "palette(highlighted-text)"}

SEMANTIC = {"#4caf50", "#f44336"}

_PALETTE = re.compile(r"palette\([a-z-]+\)")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9A-Za-z_])")
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[,)]")
_PINS_A_SIZE = re.compile(r"\b(?:min|max)-(?:width|height)\s*:(?!\s*0)")
_SUB_CONTROL = re.compile(r"::[a-z-]+")


def styled_sources():
    for directory in STYLED:
        for name in sorted(os.listdir(directory)):
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as handle:
                    yield os.path.relpath(path, ROOT), handle.read()


def qss_strings(source):
    """
    Every string literal that is not a docstring — that is where the
    stylesheets live. Prose is allowed to name the thing it is banning.
    """
    tree = ast.parse(source)
    prose = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                prose.add(id(first.value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in prose:
            yield node.value


def test_no_stylesheet_asks_the_palette_for_a_colour():
    offenders = {}
    for path, source in styled_sources():
        found = {role for text in qss_strings(source) for role in _PALETTE.findall(text)}
        found -= ALLOWED_ROLES
        if found:
            offenders[path] = sorted(found)

    assert not offenders, (
        "the palette is a light-theme colour source under FreeCAD's dark theme; "
        "delete these declarations rather than re-role them: %s" % offenders)


def test_only_the_icon_tint_still_reads_a_palette():
    """
    `pal.color(QPalette.Window)` is the same request written in Python, and it
    is worse: it resolves once at construction and cannot follow a theme change.

    One site keeps it. An SVG has to be handed a literal colour before it is
    rasterised, so the icons read the foreground of the button they sit on —
    after FreeCAD's global stylesheet has been applied to it, which is what
    makes that different from QApplication's palette. It is the documented
    exception #12 anticipated, and this test is what keeps it at one.
    """
    offenders = {}
    for path, source in styled_sources():
        roles = [
            node.attr for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute) and node.value.attr == "QPalette"
        ]
        if roles:
            offenders[path] = sorted(set(roles))

    assert set(offenders) <= {os.path.join("src", "ui", "ui_helpers.py")}, \
        "colours resolved from QPalette in Python: %s" % offenders


def test_the_only_hardcoded_colours_are_the_ones_that_carry_meaning():
    offenders = {}
    for path, source in styled_sources():
        found = {hex_value.lower()
                 for text in qss_strings(source) for hex_value in _HEX.findall(text)}
        found -= SEMANTIC
        if found:
            offenders[path] = sorted(found)

    assert not offenders, (
        "a hardcoded colour that is not success or failure is style, and style "
        "belongs to the user's theme: %s" % offenders)


def test_a_translucent_wash_is_grey_or_it_is_a_colour():
    """
    Where something must separate from its surroundings — the user's turn, a
    code block, the Plan Gate — the wash is `rgba(127, 127, 127, ...)`. Equal
    channels darken a light theme and lighten a dark one without deciding what
    either of them is. Unequal channels are a colour wearing an alpha.

    The destructive-action wash is derived from #f44336, which is semantic, so
    it is filtered out.
    """
    offenders = {}
    for path, source in styled_sources():
        tinted = {channels for text in qss_strings(source)
                  for channels in _RGBA.findall(text) if len(set(channels)) > 1}
        tinted = {c for c in tinted if "#%02x%02x%02x" % tuple(int(v) for v in c)
                  not in SEMANTIC}
        if tinted:
            offenders[path] = sorted(tinted)

    assert not offenders, "these washes are colours, not greys: %s" % offenders


def test_a_rule_that_pins_a_size_also_resets_the_box():
    """
    Qt's min-width/max-width size the *content* rect, so the theme's padding and
    border are added on top of them. `#SendButton` asked for 30px and landed at
    36px with a border it never asked for, because it never reset either.
    """
    offenders = []
    for path, source in styled_sources():
        for text in qss_strings(source):
            for selector, rule in re.findall(r"([^{}]*)\{([^{}]*)\}", text):
                if _SUB_CONTROL.search(selector):
                    continue
                if _PINS_A_SIZE.search(rule) and not (
                        "padding:" in rule and "border:" in rule):
                    offenders.append("%s: %s" % (path, " ".join(rule.split())))

    assert not offenders, (
        "these rules pin a dimension without resetting the padding and border "
        "the theme adds to it: %s" % offenders)


def test_every_button_rule_says_how_narrow_its_button_may_be():
    """
    FreeCAD's own stylesheet is `QPushButton { min-width: 80px; }`
    (`Gui/Stylesheets/FreeCAD.qss`), and an id rule only overrides the
    properties it names. So a button rule that is silent about `min-width`
    does not get the width its content asks for -- it gets eighty pixels of
    content box plus whatever padding it declared, and `setFixedSize` does not
    save it, because a stylesheet minimum feeds `minimumSizeHint`.

    Measured: the composer's attach button asked for 20px and was 80; the
    model and effort pills asked for their words and were 88 each, which is
    three quarters of a docked panel spent on `+ sonnet medium`. The eye in
    the Preferences key field asked for 32 and was 80.

    A pseudo-state rule (`:hover`) is exempt: it cascades onto the base rule
    rather than replacing it, so the base rule is the one that has to speak.
    """
    offenders = []
    for path, source in styled_sources():
        for text in qss_strings(source):
            for selector, rule in re.findall(r"([^{}]*)\{([^{}]*)\}", text):
                parts = [part.strip() for part in selector.split(",")]
                if not any(part.startswith("QPushButton") and ":" not in part
                           for part in parts):
                    continue
                if "min-width:" not in rule:
                    offenders.append("%s: %s" % (path, " ".join(selector.split())))

    assert not offenders, (
        "FreeCAD's theme gives every QPushButton an 80px minimum width; these "
        "rules never said otherwise, so their buttons are eighty pixels wide "
        "whatever they asked for: %s" % offenders)
