"""
The Sessions and the rules about them, with no Qt in the room.

A Session is durable: a name, when it was created, the Document it worked on,
and the path of its event log. The index lives in the settings manager, the
transcript stays in the log the event stream already writes per event. Nothing
about a Session's content is held in a second place.

These rules — which Session is in the index, which may be renamed by a
generated title, what is pruned off the tail, whose transcript is due — used to
live inside a `QWidget`, so the only way to reach them was to build a dock. The
tests that did paid for it: they set up by reaching into the record dicts by
hand. None of it needs a widget, so none of it is behind one any more.

Where the index and the transcripts actually live is a seam. `SettingsIndex` is
the real one, FreeCAD's settings and its user directory; the tests pass a list
and a temp directory. `ui/multi_tab_manager.py` holds the other half — one
chat area per Session, at the same positions.
"""
import os
import time
import uuid

from core import settings

KEEP = 50


def discard(path):
    """Delete means gone, not hidden — but a log already missing is fine."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass  # except-pass-allowed: a log already missing is fine


def _wants_a_title(session) -> bool:
    """In the picker under a fallback name, and nobody has overruled it."""
    return bool(session and session["indexed"]
                and not session.get("named") and not session.get("manual"))


def _matches(record, session) -> bool:
    """
    *session* is the typed identity `.id`, not the record's own log path.
    A bare log-path string is still accepted, matched against `log`.
    """
    if isinstance(session, str):
        return record["log"] == session
    return record.get("session") is not None and record["session"].id == session.id


class SettingsIndex:
    """Where a Session's index and transcripts really live."""

    def read(self) -> list:
        return settings.instance().get_sessions()

    def write(self, records):
        settings.instance().set_sessions(records)

    def new_log(self) -> str:
        import FreeCAD

        directory = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "FreeCadCopilot",
                                 "logs", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "%s.jsonl" % uuid.uuid4().hex)


class Sessions:
    """
    Every Session this process knows about, in the order they are shown.

    A position is the whole address of a Session — the same position the stack
    of chat areas uses — so anything that moves them says which moved.
    """

    def __init__(self, index=None):
        self._index = index if index is not None else SettingsIndex()
        self._sessions = []

    def _save(self):
        """
        Write the index.

        A Session that has had no Intent is not in it.
        """
        self._index.write([
            {key: session[key]
             for key in ("name", "created_at", "document", "document_id", "log")}
            for session in self._sessions if session.get("indexed")
        ])

    def load(self) -> int:
        """
        Rebuild from the index, newest last, and prune the tail.

        Only the records. Whatever shows a Session is the caller's, and filling
        it is deferred — a Session with hundreds of Steps must not be replayed
        because the panel opened.
        """
        stored = sorted(self._index.read(), key=lambda s: s.get("created_at") or 0)
        for stale in stored[:-KEEP]:
            discard(stale.get("log"))
        for record in stored[-KEEP:]:
            self._append(dict(record, indexed=True))
        if self._sessions:
            self._save()
        return self.count()

    def add(self, name: str = "New Chat", document: str = "") -> int:
        """A Session that has not been used yet. Returns its position."""
        return self._append({
            "name": name,
            "created_at": time.time(),
            "document": document,
            "document_id": "",
            "log": self._index.new_log(),
            "restored": True,
            "indexed": False,
        })

    def _append(self, record) -> int:
        from core.llm import Session

        session = dict(record)
        session.setdefault("restored", False)
        session.setdefault("document_id", "")
        session.setdefault("session", Session())
        self._sessions.append(session)
        return len(self._sessions) - 1

    def begin(self, index: int, name: str):
        """
        The Session's first Intent: it takes a name and enters the picker.

        Returns `(started, dropped)`. `started` is False once it already has,
        so a second Intent changes nothing. `dropped` is the positions pruned
        off the tail to make room, oldest first — the caller owns whatever it
        hung on those positions and has to let go of them.

        This is where the index grows, so this is where it is bounded. Pruning
        moves the Sessions after it, so the caller must re-read the live
        position rather than reuse the one it passed in.
        """
        session = self.at(index)
        if session is None or session["indexed"]:
            return False, []

        dropped = self._prune(KEEP - 1)
        if not session.get("manual"):
            session["name"] = name
        session["indexed"] = True
        self._save()
        return True, dropped

    def _prune(self, keep: int) -> list:
        """Drop the oldest indexed Sessions past the bound, transcripts included."""
        dropped = []
        indexed = [s for s in self._sessions if s.get("indexed")]
        while len(indexed) > keep:
            position = self._sessions.index(indexed.pop(0))
            if self.remove(position):
                dropped.append(position)
        return dropped

    def wants_a_title(self, index: int) -> bool:
        """Still on its fallback name, so the generated title is worth asking for."""
        return _wants_a_title(self.at(index))

    def title_arrived(self, session_id, title: str):
        """
        The generated title, taken once and then frozen. A rename outranks it.

        Keyed by the Session's own typed identity, not the position or the
        log path: the call outlives the position, and a Session deleted while
        one was in flight must not rename its neighbour. Returns the position
        renamed, or None.
        """
        record = next((s for s in self._sessions if _matches(s, session_id)), None)
        if not _wants_a_title(record) or not title.strip():
            return None
        record["name"] = title.strip()
        record["named"] = True
        self._save()
        return self._sessions.index(record)

    def rename(self, index: int, new_name: str) -> bool:
        """
        The log's path does not follow the name, so a rename cannot orphan it.

        A name the user chose is marked as theirs, and is never replaced by a
        generated one, now or later.
        """
        if not 0 <= index < len(self._sessions) or not new_name.strip():
            return False
        self._sessions[index]["name"] = new_name.strip()
        self._sessions[index]["manual"] = True
        self._save()
        return True

    def name_document(self, session_id, document: str, document_id: str = "") -> bool:
        """
        Record which Document a Session worked on, so the picker can say so.

        Two things are kept because they answer two questions. The name is
        what the user calls the file and is what the picker shows. The id is
        the Document's own `Uid`, and it is what a Document is recognised by:
        FreeCAD calls every unsaved Document "Unnamed", so a Session that ran
        against yesterday's unsaved Document is matched by name to the empty
        one created this morning, and the panel opens the wrong chat.

        Matched by the Session's own typed identity, the same as `title_arrived`.
        """
        for record in self._sessions:
            if not _matches(record, session_id):
                continue
            if record["document"] == document and record.get("document_id") == document_id:
                return False
            record["document"] = document
            record["document_id"] = document_id
            self._save()
            return True
        return False

    def remove(self, index: int) -> bool:
        if not 0 <= index < len(self._sessions):
            return False
        discard(self._sessions.pop(index).get("log"))
        self._save()
        return True

    def at(self, index: int):
        if 0 <= index < len(self._sessions):
            return self._sessions[index]
        return None

    def count(self) -> int:
        return len(self._sessions)

    def indexed(self) -> list:
        """`(position, session)` for every Session that has had an Intent."""
        return [(position, session)
                for position, session in enumerate(self._sessions)
                if session.get("indexed")]

    def mark_restored(self, index: int) -> bool:
        """True the first time a Session is asked for; its transcript is due."""
        session = self.at(index)
        if session is None or session["restored"]:
            return False
        session["restored"] = True
        return True
