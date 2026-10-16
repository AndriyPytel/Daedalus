"""Guards the package split's own acceptance criterion.

Every module under `core/llm/` stays under the split's own ceiling, so the
concern it holds stays a single read.
"""
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "src", "core", "llm")
