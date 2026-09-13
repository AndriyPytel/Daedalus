"""
Guard for the corruption FCC-252 describes: FreeCAD's whole configuration
directory, not just the group `SettingsManager` owns inside it, must never
resolve to the user's real preferences directory during a test run.
`tests/conftest.py` redirects it with `FREECAD_USER_HOME` before FreeCAD is
ever imported, so nothing in the suite can reach `user.cfg`.
"""
import pathlib

import pytest

pytestmark = pytest.mark.freecad


def test_freecads_config_directory_is_never_the_users_real_one():
    import FreeCAD

    config_dir = pathlib.Path(FreeCAD.ConfigGet("UserAppData")).resolve()
    real_home = pathlib.Path.home().resolve()

    assert config_dir != real_home and real_home not in config_dir.parents, (
        f"FreeCAD is reading and writing {config_dir}, inside the real home "
        "directory, instead of a throwaway one"
    )
