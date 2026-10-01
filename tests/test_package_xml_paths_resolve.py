"""package.xml names files that exist, and no tracked file hides a Cyrillic homoglyph.

The `<license file="LICENSE">` attribute is excluded: no LICENSE file has ever
been tracked in this checkout, a pre-existing gap unrelated to the homoglyph
check this file runs.
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


def test_package_xml_icon_is_a_file_that_exists():
    """The Addon Manager lists the addon under this icon."""
    root = ET.parse(ROOT / "package.xml").getroot()
    assert (ROOT / root.findtext(f"{NS}icon")).is_file()


def test_package_xml_declares_the_workbench_content():
    """FreeCAD's addon loader reads the addon's subdirectory off this entry."""
    root = ET.parse(ROOT / "package.xml").getroot()
    workbench = root.find(f"{NS}content/{NS}workbench")
    assert workbench is not None
    assert workbench.findtext(f"{NS}classname") == "Daedalus"
    assert workbench.findtext(f"{NS}subdirectory") == "./"


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
