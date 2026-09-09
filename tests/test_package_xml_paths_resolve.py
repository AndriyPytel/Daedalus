"""package.xml names files that exist, and no tracked file hides a Cyrillic homoglyph — FCC-357.

The `<license file="LICENSE">` attribute is excluded: no LICENSE file has ever
been tracked in this checkout, a pre-existing gap unrelated to the Cyrillic
homoglyph this ticket fixes.
"""
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = "{https://wiki.freecad.org/Package_Metadata}"

LATIN = re.compile(r"[A-Za-z]")
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
TOKEN = re.compile(r"[A-Za-z\u0400-\u04FF]+")


def _mixed_script_tokens(text):
    """Tokens that mix Latin and Cyrillic letters — the homoglyph-substitution shape."""
    return [t for t in TOKEN.findall(text) if LATIN.search(t) and CYRILLIC.search(t)]


def _tracked_files():
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return [ROOT / path for path in listed]


def test_package_xml_icon_resolves():
    root = ET.parse(ROOT / "package.xml").getroot()
    icon = root.find(f"{NS}icon").text
    assert (ROOT / icon).is_file(), icon


def test_no_tracked_file_carries_a_cyrillic_homoglyph():
    offenders = []
    for path in _tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if _mixed_script_tokens(relative):
            offenders.append(relative)
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _mixed_script_tokens(text):
            offenders.append(relative)
    assert not offenders, offenders
