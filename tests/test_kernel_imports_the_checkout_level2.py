"""
Level 2 -- a Step imports the checkout it was run from (FCC-334).

Importing FreeCAD prepends the Mod directory the addon is symlinked into, so
`core` inside the kernel meant the installed copy: a Step importing a module
this branch deleted still resolved, and `test_preflight_level2`'s
byte-identical render check could compare the installed addon against itself.
The worker puts its own `src/` in front before it serves anything.
"""
import os

import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

CHECKOUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_core_module_a_step_imports_comes_from_this_checkout(kernel):
    result = kernel.check(
        "import core.loop\n"
        "where = core.loop.__file__\n"
        "assert where.startswith(%r), where" % (CHECKOUT + os.sep,)
    )

    assert result.ok, result.traceback


def test_a_module_deleted_on_this_branch_does_not_resolve_from_the_installed_addon(kernel):
    result = kernel.check("import core.freecad_tools")

    assert not result.ok
    assert "ModuleNotFoundError" in result.traceback
