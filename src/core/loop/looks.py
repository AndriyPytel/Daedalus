"""Type cache for FreeCAD object type lookups."""
import json
import os
import tempfile


class Looks:
    """
    Every look this run has taken, so none of them is paid for twice.

    A recorded run spent 26 minutes and $4.24 of $19.46 on six describe_type
    calls — facts about the build that never change.
    """

    ABOUT_THE_BUILD = ("describe_type", "supported_types", "materials")

    CARRIED = 8

    def __init__(self, cache_path=None, freecad_version=None):
        self._taken = []
        self._step = 0
        self._cache_path = cache_path
        self._freecad_version = freecad_version
        self._disk_cache = self._load_disk_cache() if cache_path else {}

    def _load_disk_cache(self):
        """
        Read describe_type answers from disk, or {} if absent or unreadable.

        A corrupted file is treated as absent: the run continues and the Tool
        is called.
        """
        if not self._cache_path or not os.path.isfile(self._cache_path):
            return {}
        try:
            with open(self._cache_path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_disk_cache(self, name, arguments, result):
        """
        Write one describe_type answer to disk, atomically.

        Concurrent runs both write; a fresh read merged into this session's
        own entries just before the write, then a unique temporary name plus
        os.replace, keeps a session's own snapshot from erasing another's.
        """
        if not self._cache_path or name != "describe_type":
            return

        key = self._disk_cache_key(arguments)
        self._disk_cache[key] = result

        try:
            directory = os.path.dirname(self._cache_path)
            os.makedirs(directory, exist_ok=True)
            merged = {**self._load_disk_cache(), **self._disk_cache}
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(merged, handle, indent=2, sort_keys=True)
                os.replace(tmp_path, self._cache_path)
            except OSError:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        except OSError:
            pass

    def _disk_cache_key(self, arguments):
        """Cache key for describe_type: (version, type_id)."""
        args_str = json.dumps(arguments, sort_keys=True, default=str)
        return "%s:%s" % (self._freecad_version, args_str)

    def a_step_landed(self):
        """A Step applied, so what was read off the Document may have moved."""
        self._step += 1

    def _key(self, name, arguments):
        return name, json.dumps(arguments, sort_keys=True, default=str)

    def _current(self, name):
        return None if name in self.ABOUT_THE_BUILD else self._step

    def remember(self, name, arguments, result):
        key = self._key(name, arguments)
        self._taken = [taken for taken in self._taken if taken["key"] != key]
        self._taken.append({"key": key, "step": self._current(name), "tool": name,
                            "arguments": arguments, "result": result})
        self._save_disk_cache(name, arguments, result)

    def answer(self, name, arguments):
        """
        What this run already knows about this exact look, or None.

        Only where it is still true: an answer read off a Document two Steps
        ago is not an answer about this one.

        describe_type reads from disk first, then from this run's memory.
        """
        if name == "describe_type" and self._cache_path:
            disk_key = self._disk_cache_key(arguments)
            if disk_key in self._disk_cache:
                return self._disk_cache[disk_key]

        key = self._key(name, arguments)
        current = self._current(name)
        for taken in reversed(self._taken):
            if taken["key"] == key and taken["step"] == current:
                return taken["result"]
        return None

    def still_true(self):
        """
        What Execute is given without having to ask, most recent last.

        Everything about the build, and everything read off the Document since
        the last Step landed.
        """
        standing = [taken for taken in self._taken
                    if taken["step"] is None or taken["step"] == self._step]
        return [{"tool": taken["tool"], "arguments": taken["arguments"],
                 "result": taken["result"]}
                for taken in standing[-self.CARRIED:]]


