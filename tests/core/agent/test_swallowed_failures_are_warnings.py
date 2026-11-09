"""
Level 1 -- what the reader arm drops on the part path is said as a WARNING event, and the behaviour is unchanged.
"""
import json
from types import SimpleNamespace

from core.agent.reader import assert_part, parse_table, run_reader
from core.events import WARNING, EventStream
from core.llm import ScriptedLLMClient
from core.sheets import sheet_reader
from tests.event_helpers import events_of


def _said(events):
    return "\n".join(e.data["message"] for e in events_of(events, WARNING))


def test_a_table_field_that_is_not_usable_is_named_in_a_warning():
    events = EventStream()

    table = parse_table('{"overall": {"x": "n/a", "y": 5, "z": 6}, "through_count": "many", "dimensions": 3}',
                        events)

    assert table["overall"]["x"] is None and table["through_count"] is None
    said = _said(events)
    assert "overall.x" in said and "through_count" in said and "dimensions" in said


def test_a_usable_table_warns_of_nothing():
    events = EventStream()

    parse_table('{"overall": {"x": 1, "y": 5, "z": 6}, "through_count": 2}', events)

    assert not events_of(events, WARNING)


class Kernel:
    def __init__(self, drawn_ok):
        self.drawn_ok = drawn_ok

    def check(self, code, render_path=None, **_):
        if render_path:
            return SimpleNamespace(ok=self.drawn_ok, render=None, traceback="no")
        return SimpleNamespace(ok=True, stdout="FACTS " + json.dumps({"solid": False}))


def test_a_view_the_table_names_but_the_sheet_cannot_show_is_a_warning():
    events = EventStream()
    sheet = SimpleNamespace(crops=[SimpleNamespace(path="/x.png", name="a")])
    table = {"views": [{"view": "front", "crop": 5}, {"view": "detail C", "crop": 1}]}

    assert_part(Kernel(True), sheet, table, "/tmp", events)

    said = _said(events)
    assert "front" in said and "crop 5" in said and "detail C" in said


def test_a_view_that_could_not_be_drawn_is_a_warning():
    events = EventStream()
    sheet = SimpleNamespace(crops=[SimpleNamespace(path="/x.png", name="a")])

    assert_part(Kernel(False), sheet, {"views": [{"view": "front", "crop": 1}]}, "/tmp", events)

    assert "front" in _said(events)


def test_a_sheet_that_cannot_be_read_is_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(sheet_reader, "pymupdf", None)
    sheet = tmp_path / "drawing.pdf"
    sheet.write_bytes(b"%PDF-1.4")
    events = EventStream()

    run_reader("go", llm=ScriptedLLMClient([json.dumps({"finish": "x"})] * 3), kernel=Kernel(True),
               tools=None, events=events, attachments=[str(sheet)], cancelled=lambda: True)

    assert "drawing.pdf" in _said(events)
