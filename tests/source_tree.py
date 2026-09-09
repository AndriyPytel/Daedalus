"""
The packages a source-tree scan should cover, in one place so a test that
walks them derives its list rather than hardcoding one.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PACKAGES = ("core", "ui", "utils", "tools", "Mod")
