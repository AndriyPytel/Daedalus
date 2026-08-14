"""The addon has no colours of its own (#12).FreeCAD ships its themes as one global stylesheet and never touches QPalette,so the palette stays whatever the OS handed over — light values on macOS, undera dark theme. Every `palette(base)` the addon wrote was a request for thoselight values, painted over a dark panel. The fix is deletion, not better roles,and deletion is exactly the kind of thing that creeps back one rule at a time.Hence a test rather than a review note.Qt behaviour itself stays Level 3 — a manual script. What is checked here iswhat the source says, which is where this defect lives.ALLOWED_ROLE is the one role FreeCAD's themes do keep meaningful, and the oneplace the addon still needs it: the Step that is running.SEMANTIC holds colour that carries meaning rather than style. These must notfollow the theme (a failed Step is red in every theme), so they stay explicitvalues.Sub-control rules (`::handle`, `::drop-down`) are drawn by the style, not boxedby the theme's widget padding, so the reset does not apply to them."""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLED = [os.path.join(ROOT, "ui"), os.path.join(ROOT, "Mod", "pages")]

ALLOWED_ROLE = "palette(highlight)"

SEMANTIC = {"#4caf50", "#f44336"}

_PALETTE = re.compile(r"palette\([a-z-]+\)")
_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9A-Za-z_])")
_RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*[,)]")
_PINS_A_SIZE = re.compile(r"\b(?:min|max)-(?:width|height)\s*:")
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
        found.discard(ALLOWED_ROLE)
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

    assert set(offenders) <= {os.path.join("ui", "ui_helpers.py")}, \
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
