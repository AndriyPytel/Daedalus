"""Guards Init.py.__version__ against package.xml."""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_package_xml():
    spec = importlib.util.spec_from_file_location("_init", ROOT / "Init.py")
    init = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(init)
    package_version = ET.parse(ROOT / "package.xml").getroot().find("{https://wiki.freecad.org/Package_Metadata}version").text
    assert init.__version__ == package_version
