"""
The Sessions and the rules about them, with no Qt in the room: a Session is
durable -- a name, when it was created, the Document it worked on, and the
path of its event log -- with nothing about its content held in a second
place, and it is addressed by one id, the uuid in its log's file name.
These rules -- which Session is in the index, which may be renamed by a
generated title, what is pruned off the tail, whose transcript is due
-- live here rather than inside a `QWidget`, so reaching them costs no dock.
Where the index and the transcripts actually live is a seam: `SettingsIndex`
is the real one, FreeCAD's settings and its user directory, while
`ui/multi_tab_manager.py` holds the other half, one chat area per Session at
the same ids.
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


def log_id(log: str) -> str:
    """The Session's id: the uuid in the file name of its log."""
    return os.path.splitext(os.path.basename(log))[0]


class SettingsIndex:
    """Where a Session's index and transcripts really live."""

    def read(self) -> list:
        return settings.instance().get_sessions()

    def write(self, records):
        settings.instance().set_sessions(records)

    def new_log(self) -> str:
        import FreeCAD

        directory = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "Daedalus",
                                 "logs", "sessions")
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "%s.jsonl" % uuid.uuid4().hex)


class Sessions:
    """
    Every Session this process knows about, in the order they are shown.

    The id is the whole address of a Session, so pruning or removing another
    one cannot move it.
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

    def add(self, name: str = "New Chat", document: str = "") -> str:
        """A Session that has not been used yet. Returns its id."""
        return self._append({
            "name": name,
            "created_at": time.time(),
            "document": document,
            "document_id": "",
            "log": self._index.new_log(),
            "restored": True,
            "indexed": False,
        })

    def _append(self, record) -> str:
        session = dict(record, id=log_id(record["log"]))
        session.setdefault("restored", False)
        session.setdefault("document_id", "")
        self._sessions.append(session)
        return session["id"]

    def begin(self, session_id: str, name: str):
        """
        The Session's first Intent: it takes a name and enters the picker,
        returning `(started, dropped)`. `started` is False once it already
        has, so a second Intent changes nothing; `dropped` is the ids
        pruned off the tail to make room, oldest first, and the caller owns
        whatever it hung on those ids. This is where the index grows, so
        this is where it is bounded.
        """
        session = self.at(session_id)
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
            oldest = indexed.pop(0)["id"]
            if self.remove(oldest):
                dropped.append(oldest)
        return dropped

    def wants_a_title(self, session_id: str) -> bool:
        """Still on its fallback name, so the generated title is worth asking for."""
        return _wants_a_title(self.at(session_id))

    def title_arrived(self, session_id: str, title: str) -> bool:
        """
        The generated title, taken once and then frozen; a rename outranks
        it. Keyed by the id, which outlives every position, so a Session
        deleted mid-flight cannot rename its neighbour.
        """
        record = self.at(session_id)
        if not _wants_a_title(record) or not title.strip():
            return False
        record["name"] = title.strip()
        record["named"] = True
        self._save()
        return True

    def rename(self, session_id: str, new_name: str) -> bool:
        """
        The log's path does not follow the name, so a rename cannot orphan it.

        A name the user chose is marked as theirs, and is never replaced by a
        generated one, now or later.
        """
        record = self.at(session_id)
        if record is None or not new_name.strip():
            return False
        record["name"] = new_name.strip()
        record["manual"] = True
        self._save()
        return True

    def name_document(self, session_id: str, document: str, document_id: str = "") -> bool:
        """
        Record which Document a Session worked on, so the picker can say
        so, keeping two things that answer two questions. The name is what
        the user calls the file and is what the picker shows; the id is the
        Document's own `Uid`, since FreeCAD calls every unsaved Document
        "Unnamed" and matching by name alone would open the wrong chat.
        """
        record = self.at(session_id)
        if record is None:
            return False
        if record["document"] == document and record.get("document_id") == document_id:
            return False
        record["document"] = document
        record["document_id"] = document_id
        self._save()
        return True

    def remove(self, session_id: str) -> bool:
        record = self.at(session_id)
        if record is None:
            return False
        self._sessions.remove(record)
        discard(record.get("log"))
        self._save()
        return True

    def at(self, session_id: str):
        return next((s for s in self._sessions if s["id"] == session_id), None)

    def ids(self) -> list:
        return [session["id"] for session in self._sessions]

    def count(self) -> int:
        return len(self._sessions)

    def indexed(self) -> list:
        """Every Session that has had an Intent, in the order they are shown."""
        return [session for session in self._sessions if session.get("indexed")]

    def mark_restored(self, session_id: str) -> bool:
        """True the first time a Session is asked for; its transcript is due."""
        session = self.at(session_id)
        if session is None or session["restored"]:
            return False
        session["restored"] = True
        return True
