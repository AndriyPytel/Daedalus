"""FreeCadCopilot module."""
# --- BEGIN VENDORING ---
# This block ensures that our bundled 'lib' directory is added to
# the Python path, so we can import our dependencies (langchain, etc.)
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
