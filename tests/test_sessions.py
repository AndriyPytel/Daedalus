"""
Level 1 — the Sessions.

The durable index, the naming, the bound on how many are kept. These rules used
to sit inside a `QWidget`, so checking one meant building a dock and reaching
into its record dicts; they need no Qt at all, and this proves it.
"""
import os

import pytest

from core import sessions as sessions_module
from core.sessions import Sessions


class FakeIndex:
    """The index as a plain list, and the transcripts in a temp directory."""

    def __init__(self, directory):
        self.records = []
        self._directory = directory
        self._made = 0

    def read(self):
        return list(self.records)

    def write(self, records):
        self.records = list(records)

    def new_log(self):
        self._made += 1
        path = os.path.join(self._directory, "session%d.jsonl" % self._made)
        open(path, "w").close()
        return path


@pytest.fixture
def index(tmp_path):
    return FakeIndex(str(tmp_path))


@pytest.fixture
def sessions(index):
    return Sessions(index)


def _with_an_intent(sessions, name):
    """A Session that has been used, which is the only kind that is durable."""
    position = sessions.add(name)
    sessions.begin(position, name)
    return position


def test_a_session_with_no_intent_is_not_in_the_index(sessions, index):
    """
    It has nothing to show in the picker and nothing to be named after, and
    every launch would otherwise add one (FCC-42).
    """
    sessions.add("New Chat")

    assert index.records == []
    assert sessions.count() == 1, "it still exists, it is just not durable"


def test_the_first_intent_puts_a_session_in_the_index(sessions, index):
    position = sessions.add("New Chat")

    started, dropped = sessions.begin(position, "A 20-tooth gear")

    assert started and dropped == []
    assert [record["name"] for record in index.records] == ["A 20-tooth gear"]


def test_a_second_intent_changes_nothing(sessions, index):
    position = _with_an_intent(sessions, "A 20-tooth gear")

    started, _ = sessions.begin(position, "Something else entirely")

    assert not started
    assert [record["name"] for record in index.records] == ["A 20-tooth gear"]


def test_the_index_carries_only_the_durable_fields(sessions, index):
    """Whatever shows a Session is the caller's; none of it is written down."""
    _with_an_intent(sessions, "A 20-tooth gear")

    assert set(index.records[0]) == {"name", "created_at", "document",
                                     "document_id", "log"}


def test_a_document_is_recognised_by_its_own_id_not_by_its_name(sessions):
    """
    FreeCAD calls every unsaved Document "Unnamed": a Session is matched to
    the Document it worked on by the id, or a new empty Document reopens an
    old chat.
    """
    position = sessions.add("Flange")
    session_id = sessions.at(position)["session"]
    sessions.begin(position, "Flange")

    assert sessions.name_document(session_id, "Unnamed", "3a991267-6863")
    assert sessions.at(position)["document"] == "Unnamed"
    assert sessions.at(position)["document_id"] == "3a991267-6863"


def test_a_session_that_never_ran_has_no_document_id(sessions):
    """Nothing was sent, so nothing recognises it: it matches no Document."""
    position = sessions.add("Flange")

    assert sessions.at(position)["document_id"] == ""


def test_the_session_bound_holds_without_a_restart(sessions, index, monkeypatch):
    """Creating a Session prunes too, so a FreeCAD left open stays bounded. FCC-17."""
    monkeypatch.setattr(sessions_module, "KEEP", 3)
    logs = []
    for n in range(5):
        position = sessions.add("Chat %d" % n)
        logs.append(sessions.at(position)["log"])
        sessions.begin(position, "Chat %d" % n)

    assert sessions.count() == 3
    assert [session["name"] for _, session in sessions.indexed()] == \
        ["Chat 2", "Chat 3", "Chat 4"]
    assert not os.path.exists(logs[0]), "the pruned Session kept its transcript"
    assert os.path.exists(logs[4])
    assert len(index.records) == 3


def test_begin_says_which_positions_it_pruned(sessions, monkeypatch):
    """
    The caller hung a chat area on each of them and has to let go of it, so
    pruning that said nothing would leak a page per evicted Session.
    """
    monkeypatch.setattr(sessions_module, "KEEP", 2)
    for n in range(2):
        _with_an_intent(sessions, "Chat %d" % n)

    position = sessions.add("Chat 2")
    _, dropped = sessions.begin(position, "Chat 2")

    assert dropped == [0]


def test_a_launch_with_no_intent_prunes_nothing(sessions, index, monkeypatch):
    """
    FCC-17 prunes when a Session is created; FCC-42 stopped writing Sessions
    that never had an Intent. Counting those against the bound would evict a
    real Session, transcript included, on every launch where nobody typed.
    """
    monkeypatch.setattr(sessions_module, "KEEP", 3)
    for n in range(3):
        _with_an_intent(sessions, "Real %d" % n)
    kept = [record["name"] for record in index.records]

    sessions.add("New Chat")

    assert [record["name"] for record in index.records] == kept


def test_load_keeps_the_newest_and_deletes_the_rest(index, monkeypatch, tmp_path):
    monkeypatch.setattr(sessions_module, "KEEP", 2)
    logs = []
    for n in range(4):
        log = str(tmp_path / ("old%d.jsonl" % n))
        open(log, "w").close()
        logs.append(log)
        index.records.append({"name": "Chat %d" % n, "created_at": n,
                              "document": "", "log": log})

    loaded = Sessions(index)
    assert loaded.load() == 2

    assert [session["name"] for _, session in loaded.indexed()] == ["Chat 2", "Chat 3"]
    assert not os.path.exists(logs[0]) and not os.path.exists(logs[1])
    assert os.path.exists(logs[3])


def test_a_generated_title_is_taken_once_and_then_frozen(sessions):
    position = _with_an_intent(sessions, "New Chat")
    session_id = sessions.at(position)["session"]

    assert sessions.title_arrived(session_id, "A 20-tooth gear") == position
    assert sessions.title_arrived(session_id, "Something else") is None
    assert sessions.at(position)["name"] == "A 20-tooth gear"


def test_a_log_rename_does_not_break_a_titles_match(sessions):
    """The join is the typed identity, not the path — a moved log must not orphan it."""
    position = _with_an_intent(sessions, "New Chat")
    session_id = sessions.at(position)["session"]

    sessions.at(position)["log"] = "/elsewhere/renamed.jsonl"

    assert sessions.title_arrived(session_id, "A 20-tooth gear") == position
    assert sessions.at(position)["name"] == "A 20-tooth gear"


def test_a_name_the_user_chose_outranks_a_generated_one(sessions):
    position = _with_an_intent(sessions, "New Chat")

    sessions.rename(position, "My bracket")

    assert not sessions.wants_a_title(position)
    assert sessions.title_arrived(sessions.at(position)["session"], "A 20-tooth gear") is None
    assert sessions.at(position)["name"] == "My bracket"


def test_a_title_is_keyed_by_the_log_so_it_cannot_rename_a_neighbour(sessions):
    """The call outlives the position; a Session deleted under it must not shift it."""
    first = _with_an_intent(sessions, "New Chat")
    session_id = sessions.at(first)["session"]
    _with_an_intent(sessions, "New Chat")

    sessions.remove(0)

    assert sessions.title_arrived(session_id, "A 20-tooth gear") is None, \
        "the Session it was for is gone"
    assert sessions.at(0)["name"] == "New Chat"


def test_an_empty_title_is_not_a_name(sessions):
    position = _with_an_intent(sessions, "New Chat")

    assert sessions.title_arrived(sessions.at(position)["session"], "   ") is None


def test_a_session_that_was_never_used_is_not_worth_naming(sessions):
    """It is not in the picker, so there is nothing for a title to label."""
    assert not sessions.wants_a_title(sessions.add("New Chat"))


def test_naming_the_document_is_written_down(sessions, index):
    position = _with_an_intent(sessions, "New Chat")

    assert sessions.name_document(sessions.at(position)["session"], "Gearbox")
    assert index.records[0]["document"] == "Gearbox"


def test_indexed_carries_the_position_not_a_fresh_count(sessions):
    """A filtered list numbered from zero would open its neighbour (FCC-42)."""
    sessions.add("never used")
    used = _with_an_intent(sessions, "A 20-tooth gear")

    assert sessions.indexed() == [(used, sessions.at(used))]


def test_a_transcript_is_due_once(sessions, index, tmp_path):
    index.records.append({"name": "yesterday", "created_at": 1,
                          "document": "", "log": str(tmp_path / "y.jsonl")})
    loaded = Sessions(index)
    loaded.load()

    assert loaded.mark_restored(0), "a Session off the index has not been shown yet"
    assert not loaded.mark_restored(0)


def test_a_session_made_in_this_process_has_nothing_to_restore(sessions):
    assert not sessions.mark_restored(sessions.add("New Chat"))


def test_removing_takes_the_transcript_with_it(sessions, index):
    position = _with_an_intent(sessions, "doomed")
    log = sessions.at(position)["log"]

    assert sessions.remove(position)

    assert not os.path.exists(log), "delete hid the Session and kept the transcript"
    assert index.records == []


def test_a_position_that_is_not_there_is_not_an_error(sessions):
    assert sessions.at(7) is None
    assert not sessions.remove(7)
    assert not sessions.rename(7, "x")
    assert not sessions.mark_restored(7)
    assert sessions.begin(7, "x") == (False, [])
