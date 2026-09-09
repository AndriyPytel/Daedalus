"""
FreeCadCopilot - Initialization
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import FreeCAD

__title__ = "FreeCadCopilot"
__author__ = "Andrii Pytel"
__url__ = "https://github.com/AndriyPytel/FreeCadCopilot"
__doc__ = "Kernel/sub-agent AI architecture for FreeCAD"
__date__ = "2025-10-26"
_root = ET.parse(Path(__file__).parent / "package.xml").getroot()
__version__ = _root.find("{https://wiki.freecad.org/Package_Metadata}version").text


def initialize():
    """Called by FreeCAD on startup."""
    pass


def uninitialize():
    """Called by FreeCAD on shutdown."""
    pass
