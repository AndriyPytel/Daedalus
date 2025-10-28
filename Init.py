# --- BEGIN VENDORING ---
# This block ensures that our bundled 'lib' directory is added to
# the Python path, so we can import our dependencies (langchain, etc.)


"""
FreeCadCopilot - Initialization
"""
import FreeCAD

__title__ = "FreeCadCopilot"
__author__ = "Andrii Pytel"
__url__ = "https://github.com/AndriyPytel/FreeCadCopilot"
__doc__ = "AI-powered command execution for FreeCAD"
__date__ = "2025-10-26"
__version__ = "0.1.0"


def initialize():
    """Called by FreeCAD on startup."""
    import sys
    import os

    # Get the path to the directory where this Init.py file lives
    addon_path = os.path.dirname(__file__)

    # Construct the full path to our 'lib' directory
    lib_path = os.path.join(addon_path, "lib")

    # Add the 'lib' path to sys.path if it's not already there
    if lib_path not in sys.path:
        sys.path.append(lib_path)
    # --- END VENDORING ---
    pass


def uninitialize():
    """Called by FreeCAD on shutdown."""
    pass
