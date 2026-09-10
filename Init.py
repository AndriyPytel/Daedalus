"""
FreeCadCopilot - Initialization

FreeCAD runs this file with exec(compile(source, path, "exec")) out of
FreeCADInit.InitApplications, in that module's globals. There is no __file__
here and never was; the addon directory is read off the code object of a
function compiled from this file.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import FreeCAD

__title__ = "FreeCadCopilot"
__author__ = "Andrii Pytel"
__url__ = "https://github.com/AndriyPytel/FreeCadCopilot"
__doc__ = "Kernel/sub-agent AI architecture for FreeCAD"
__date__ = "2025-10-26"


def initialize():
    """Called by FreeCAD on startup."""
    pass


def uninitialize():
    """Called by FreeCAD on shutdown."""
    pass


_root = ET.parse(Path(initialize.__code__.co_filename).parent / "package.xml").getroot()
__version__ = _root.find("{https://wiki.freecad.org/Package_Metadata}version").text
