"""
Level 1 — the Sessions.

The durable index, the naming, the bound on how many are kept. These rules used
to sit inside a `QWidget`, so checking one meant building a dock and reaching
into its record dicts; they need no Qt at all, and this proves it.
"""
import os
import uuid

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
    session_id = sessions.add(name)
    sessions.begin(session_id, name)
    return session_id


def test_a_session_with_no_intent_is_not_in_the_index(sessions, index):
    """
    It has nothing to show in the picker and nothing to be named after, and
    every launch would otherwise add one.
    """
    sessions.add("New Chat")

    assert index.records == []
    assert sessions.count() == 1, "it still exists, it is just not durable"


def test_the_first_intent_puts_a_session_in_the_index(sessions, index):
    session_id = sessions.add("New Chat")

    started, dropped = sessions.begin(session_id, "A 20-tooth gear")

    assert started and dropped == []
    assert [record["name"] for record in index.records] == ["A 20-tooth gear"]


def test_a_second_intent_changes_nothing(sessions, index):
    session_id = _with_an_intent(sessions, "A 20-tooth gear")

    started, _ = sessions.begin(session_id, "Something else entirely")

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
    session_id = sessions.add("Flange")
    sessions.begin(session_id, "Flange")

    assert sessions.name_document(session_id, "Unnamed", "3a991267-6863")
    assert sessions.at(session_id)["document"] == "Unnamed"
    assert sessions.at(session_id)["document_id"] == "3a991267-6863"


def test_a_session_that_never_ran_has_no_document_id(sessions):
    """Nothing was sent, so nothing recognises it: it matches no Document."""
    session_id = sessions.add("Flange")

    assert sessions.at(session_id)["document_id"] == ""


def test_the_session_bound_holds_without_a_restart(sessions, index, monkeypatch):
    """Creating a Session prunes too, so a FreeCAD left open stays bounded."""
    monkeypatch.setattr(sessions_module, "KEEP", 3)
    logs = []
    for n in range(5):
        session_id = sessions.add("Chat %d" % n)
        logs.append(sessions.at(session_id)["log"])
        sessions.begin(session_id, "Chat %d" % n)

    assert sessions.count() == 3
    assert [session["name"] for session in sessions.indexed()] == \
        ["Chat 2", "Chat 3", "Chat 4"]
    assert not os.path.exists(logs[0]), "the pruned Session kept its transcript"
    assert os.path.exists(logs[4])
    assert len(index.records) == 3


def test_pruning_returns_the_ids_it_dropped_and_leaves_the_others_addressable(
        sessions, monkeypatch):
    """
    The caller hung a chat area on each dropped id and has to let go of it;
    the ids that stay must still find their own Session.
    """
    monkeypatch.setattr(sessions_module, "KEEP", 3)
    first, second, third = (_with_an_intent(sessions, "Chat %d" % n) for n in range(3))

    fourth = sessions.add("Chat 3")
    _, dropped = sessions.begin(fourth, "Chat 3")

    assert dropped == [first]
    assert sessions.at(first) is None
    assert [sessions.at(kept)["name"] for kept in (second, third, fourth)] == \
        ["Chat 1", "Chat 2", "Chat 3"]


def test_a_launch_with_no_intent_prunes_nothing(sessions, index, monkeypatch):
    """
    Pruning happens when a Session is created; a separate change stopped
    writing Sessions that never had an Intent. Counting those against the bound would evict a
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

    assert [session["name"] for session in loaded.indexed()] == ["Chat 2", "Chat 3"]
    assert not os.path.exists(logs[0]) and not os.path.exists(logs[1])
    assert os.path.exists(logs[3])


def test_a_restored_session_has_the_id_it_was_saved_with(index, tmp_path):
    saved_as = uuid.uuid4().hex
    log = str(tmp_path / (saved_as + ".jsonl"))
    index.records.append({"name": "yesterday", "created_at": 1,
                          "document": "", "log": log})

    loaded = Sessions(index)
    loaded.load()

    assert loaded.ids() == [saved_as]
    assert loaded.at(saved_as)["log"] == log


def test_a_generated_title_is_taken_once_and_then_frozen(sessions):
    session_id = _with_an_intent(sessions, "New Chat")

    assert sessions.title_arrived(session_id, "A 20-tooth gear")
    assert not sessions.wants_a_title(session_id)
    assert not sessions.title_arrived(session_id, "Something else")
    assert sessions.at(session_id)["name"] == "A 20-tooth gear"


def test_a_log_rename_does_not_break_a_titles_match(sessions):
    """The join is the id, not the path — a moved log must not orphan it."""
    session_id = _with_an_intent(sessions, "New Chat")

    sessions.at(session_id)["log"] = "/elsewhere/renamed.jsonl"

    assert sessions.title_arrived(session_id, "A 20-tooth gear")
    assert sessions.at(session_id)["name"] == "A 20-tooth gear"


def test_a_name_the_user_chose_outranks_a_generated_one(sessions):
    session_id = _with_an_intent(sessions, "New Chat")

    sessions.rename(session_id, "My bracket")

    assert not sessions.wants_a_title(session_id)
    assert not sessions.title_arrived(session_id, "A 20-tooth gear")
    assert sessions.at(session_id)["name"] == "My bracket"


def test_a_name_chosen_before_the_first_intent_also_wins(sessions):
    session_id = sessions.add("New Chat")

    sessions.rename(session_id, "My bracket")
    sessions.begin(session_id, "the truncated intent")

    assert sessions.at(session_id)["name"] == "My bracket"
    assert not sessions.title_arrived(session_id, "A 20-tooth gear")


def test_a_title_cannot_rename_a_neighbour_of_a_removed_session(sessions):
    """The call outlives every other Session; one deleted under it must not shift it."""
    doomed = _with_an_intent(sessions, "New Chat")
    asker = _with_an_intent(sessions, "New Chat")

    sessions.remove(doomed)

    assert not sessions.title_arrived(doomed, "A 20-tooth gear"), \
        "the Session it was for is gone"
    assert sessions.title_arrived(asker, "A 20-tooth gear")
    assert sessions.ids() == [asker]


def test_an_empty_title_is_not_a_name(sessions):
    session_id = _with_an_intent(sessions, "New Chat")

    assert not sessions.title_arrived(session_id, "   ")


def test_a_session_that_was_never_used_is_not_worth_naming(sessions):
    """It is not in the picker, so there is nothing for a title to label."""
    assert not sessions.wants_a_title(sessions.add("New Chat"))


def test_naming_the_document_is_written_down(sessions, index):
    session_id = _with_an_intent(sessions, "New Chat")

    assert sessions.name_document(session_id, "Gearbox")
    assert index.records[0]["document"] == "Gearbox"


def test_indexed_leaves_out_the_sessions_that_never_ran(sessions):
    sessions.add("never used")
    used = _with_an_intent(sessions, "A 20-tooth gear")

    assert sessions.indexed() == [sessions.at(used)]


def test_a_transcript_is_due_once(sessions, index, tmp_path):
    index.records.append({"name": "yesterday", "created_at": 1,
                          "document": "", "log": str(tmp_path / "y.jsonl")})
    loaded = Sessions(index)
    loaded.load()
    [session_id] = loaded.ids()

    assert loaded.mark_restored(session_id), "a Session off the index has not been shown yet"
    assert not loaded.mark_restored(session_id)


def test_a_session_made_in_this_process_has_nothing_to_restore(sessions):
    assert not sessions.mark_restored(sessions.add("New Chat"))


def test_removing_takes_the_transcript_with_it(sessions, index):
    session_id = _with_an_intent(sessions, "doomed")
    log = sessions.at(session_id)["log"]

    assert sessions.remove(session_id)

    assert not os.path.exists(log), "delete hid the Session and kept the transcript"
    assert index.records == []


def test_an_id_that_is_not_there_is_not_an_error(sessions):
    assert sessions.at("nope") is None
    assert not sessions.remove("nope")
    assert not sessions.rename("nope", "x")
    assert not sessions.mark_restored("nope")
    assert sessions.begin("nope", "x") == (False, [])
