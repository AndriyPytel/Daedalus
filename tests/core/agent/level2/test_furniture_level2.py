"""
Level 2 -- Equipment on real Arch objects in headless FreeCAD: placed from a library file or as a parametric box, judged by the BIM Assertion, and written to IFC4 with ATTRIBUTION.md beside it.
"""
import json
import os
import subprocess
import sys

import pytest

from core import furniture
from core.agent.bim import measured, refusals, unfinished
from core.agent.domain import BIM
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from tests.bim_helpers import FLAT
from core.furniture import RAW
from tests.furniture_helpers import FakeGet

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "src")

pytestmark = pytest.mark.freecad

PATH = "Architectural Parts/Chairs/Office_chair.FCStd"
URL = RAW + PATH
PROV = {"Title": "Office chair", "Author": "Ada Lovelace", "Licence": "CC-BY 3.0",
        "LicenceURL": "https://creativecommons.org/licenses/by/3.0/", "Source": URL}
FIND = {"query": "chair"}


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=180) as running:
        yield running


@pytest.fixture(autouse=True)
def library_index(tmp_path, monkeypatch):
    monkeypatch.setattr(furniture, "CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(furniture, "http_get", FakeGet())


@pytest.fixture(scope="module")
def library_file(tmp_path_factory):
    import FreeCAD

    path = str(tmp_path_factory.mktemp("library") / "Office_chair.FCStd")
    document = FreeCAD.newDocument("Chair")
    document.CreatedBy = "Ada Lovelace"
    box = document.addObject("Part::Box", "Seat")
    box.Length, box.Width, box.Height = 450, 450, 900
    box.Placement.Base = FreeCAD.Vector(1000, 1000, 50)
    document.recompute()
    document.saveAs(path)
    FreeCAD.closeDocument(document.Name)
    return path


def _flat(kernel):
    kernel.check("for o in list(doc.Objects):\n    doc.removeObject(o.Name)")
    assert kernel.check(FLAT).ok


def _place(path, provenance=PROV, x=1000, y=1000, rotation=0):
    return ("from core.equipment import place_library\n"
            "place_library(doc, %r, %r, %r, %r, %r, 'Level')\ndoc.recompute()"
            % (path, provenance, x, y, rotation))


def test_a_library_item_is_placed_as_equipment_with_its_source_and_licence_under_the_level(kernel, library_file):
    _flat(kernel)

    placed = kernel.check(_place(library_file))
    assert placed.ok, placed.traceback
    facts = measured(kernel)

    assert facts["equipment"] == [{"label": "Office chair", "source": PATH, "level": True}]
    assert refusals(facts, lambda: [PATH]) == [] and unfinished(kernel) is None
    assert kernel.check(
        "o = doc.getObject('Office_chair') or doc.getObjectsByLabel('Office chair')[0]\n"
        "b = o.Shape.BoundBox\nprint(o.Licence, o.Title, round(b.XLength), round(b.YLength), round(b.ZMin), "
        "round(b.Center.x), round(b.Center.y))").stdout.split() == [
        "CC-BY", "3.0", "Office", "chair", "450", "450", "0", "1000", "1000"]


def test_the_ifc4_export_reopens_with_the_equipment_in_it(kernel, library_file):
    _flat(kernel)
    assert kernel.check(_place(library_file)).ok

    facts = measured(kernel)

    assert facts["ifc"]["schema"] == "IFC4" and facts["ifc"]["IfcFurnishingElement"] == 1


@pytest.mark.parametrize("kind", sorted(furniture.SIZES))
def test_a_parametric_item_is_a_box_of_the_kinds_typical_size_with_source_parametric(kernel, kind):
    _flat(kernel)
    code = ("from core.equipment import place_parametric\n"
            "place_parametric(doc, %r, 2000, 1500, 0, 'Level')\ndoc.recompute()" % kind)

    assert kernel.check(code).ok
    facts = measured(kernel)

    assert facts["equipment"] == [{"label": kind.capitalize(), "source": "parametric", "level": True}]
    assert refusals(facts, lambda: []) == []
    box = kernel.check("b = doc.getObjectsByLabel(%r)[0].Shape.BoundBox\n"
                       "print(round(b.XLength), round(b.YLength), round(b.ZLength), round(b.ZMin))"
                       % kind.capitalize()).stdout.split()
    assert box == [str(v) for v in (*furniture.SIZES[kind], 0)]


def test_equipment_with_a_source_outside_the_whitelist_is_refused_by_the_assertion(kernel, library_file):
    _flat(kernel)
    stray = _place(library_file, PROV | {"Source": "https://sketchfab.com/sofa.glb"})
    assert kernel.check(stray).ok

    found = unfinished(kernel)

    assert found is not None and "Office chair" in found and "Source" in found


def test_equipment_under_no_level_is_refused_by_the_assertion(kernel, library_file):
    _flat(kernel)
    assert kernel.check(_place(library_file)).ok
    assert kernel.check("o = doc.getObjectsByLabel('Office chair')[0]\n"
                        "level = doc.getObjectsByLabel('Level')[0]\nlevel.Group = [m for m in level.Group if m != o]\ndoc.recompute()").ok

    found = unfinished(kernel)

    assert found is not None and "Office chair" in found and "Level" in found


def test_the_agent_finds_places_and_finishes_and_the_ifc4_comes_with_its_attribution(
        kernel, library_file, tmp_path, monkeypatch):
    from sdk import Client

    monkeypatch.setattr(furniture, "http_get", FakeGet(body=open(library_file, "rb").read()))
    chair = furniture.find("chair", furniture.index(furniture.CACHE, furniture.http_get))[0]
    say = lambda **turn: json.dumps(turn)
    llm = ScriptedLLMClient([
        say(code=FLAT), say(find_furniture=FIND),
        say(place_furniture={"candidate": chair, "x": 1000, "y": 1000, "rotation": 0, "level": "Level"}),
        say(finish="a furnished flat")])
    client = Client(kernel=kernel, llm=llm)

    result = client.agent("Model the flat with a chair.", domain=BIM)

    assert result.ok, result.reason
    path = client.export_ifc(str(tmp_path / "flat.ifc"))
    text = open(str(tmp_path / "flat.ATTRIBUTION.md")).read()
    assert chair["title"] in text and chair["url"] in text and "Ada Lovelace" in text
    assert "CC-BY 3.0 (https://creativecommons.org/licenses/by/3.0/)" in text
    client.close()


def test_the_real_library_has_a_bed_that_can_be_placed(kernel, tmp_path):
    fetched = subprocess.run(
        [sys.executable, "-c",
         "import json, sys\nfrom core import furniture\n"
         "bed = furniture.find('bed', furniture.index(sys.argv[1], furniture.http_get))[0]\n"
         "print(json.dumps([bed, furniture.download(bed, sys.argv[1], furniture.http_get)]))", str(tmp_path)],
        capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=SRC))
    if fetched.returncode:
        pytest.skip("no network: " + fetched.stderr.strip().splitlines()[-1])
    bed, local = json.loads(fetched.stdout)
    _flat(kernel)

    assert kernel.check(_place(local, PROV | {"Title": bed["title"], "Source": bed["url"]})).ok
    assert refusals(measured(kernel), lambda: furniture.index(str(tmp_path), None)) == []
