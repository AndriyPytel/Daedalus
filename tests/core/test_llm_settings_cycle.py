"""Guards the settings/llm separation.

`core/settings.py` must not import anything from `core/llm` at all: reading
one setting should not load the whole LLM stack.
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent.parent / "src"


def test_importing_settings_does_not_import_any_llm_module():
    """settings.py should not pull in any core.llm module at load time."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import core.settings; "
         "llm_modules = [m for m in sys.modules if m.startswith('core.llm')]; "
         "assert not llm_modules, 'core.settings imported core.llm: %%s' %% llm_modules"
         % str(SRC)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_llm_py_file_no_longer_exists():
    """core/llm.py should be replaced by the core/llm/ package."""
    assert not (SRC / "core" / "llm.py").exists(), "core/llm.py should not exist"
    assert (SRC / "core" / "llm" / "__init__.py").exists(), "core/llm/ package should exist"


