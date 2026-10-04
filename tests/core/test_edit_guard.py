"""
The edit guard: one solid, valid, a volume change that is the one asked for, and a
bounding box that moves only on the sides the edit names.
"""
from core.edit_guard import collateral, measured

BASE = {"solids": 1, "valid": True, "volume": 10000.0, "bbox": (0, 0, 0, 40, 30, 10)}


def built(**changes):
    return dict(BASE, **changes)


def test_an_untouched_part_holds():
    assert collateral(BASE, built()) == []


def test_a_second_solid_fails():
    assert any("solids" in why for why in collateral(BASE, built(solids=2)))


def test_an_invalid_part_fails():
    assert any("valid" in why for why in collateral(BASE, built(valid=False)))


def test_the_volume_asked_for_holds_and_extra_material_removed_fails():
    assert collateral(BASE, built(volume=9800.0), volume_change=-200.0) == []
    assert any("volume" in why for why in
               collateral(BASE, built(volume=9300.0), volume_change=-200.0))


def test_without_a_volume_asked_a_gross_loss_fails():
    assert collateral(BASE, built(volume=9000.0)) == []
    assert any("volume" in why for why in collateral(BASE, built(volume=4000.0)))


def test_the_box_moves_only_where_asked():
    taller = built(volume=11000.0, bbox=(0, 0, 0, 40, 30, 11))
    assert any("bounding box" in why for why in collateral(BASE, taller, volume_change=1000.0))
    assert collateral(BASE, taller, volume_change=1000.0, moved=("+z",)) == []


def test_measured_reads_a_shape():
    class Box:
        Solids = [1]
        Volume = 5.0

        class BoundBox:
            XMin, YMin, ZMin, XMax, YMax, ZMax = 0, 0, 0, 1, 2, 3

        def isValid(self):
            return True

        def optimalBoundingBox(self, triangulation):
            return self.BoundBox

    assert measured(Box()) == {"solids": 1, "valid": True, "volume": 5.0,
                               "bbox": (0, 0, 0, 1, 2, 3)}
