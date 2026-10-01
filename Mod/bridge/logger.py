"""
Session-aware logging for Daedalus: one timestamped file per session,
capped at MAX_LOG_FILES, plus log_exception and generate_crash_report for the
chat UI. Every `FreeCAD.Console` write here is marshalled onto the Qt main
thread, because FreeCAD's console observers (Report View, Notification Area)
build Qt widgets and crash FreeCAD when invoked off the GUI thread. The
`FileHandler` is plain file I/O with no Qt involved, so only the two
`FreeCAD.Console` routes below are guarded.
"""
import FreeCAD
import logging
import os
import sys
import platform
import traceback
from datetime import datetime

MAX_LOG_FILES = 10
LOG_DISPATCH_TIMEOUT = 5.0

_session_log_file: str = ""


def _console(write, msg) -> None:
    """
    Call a `FreeCAD.Console.Print*` method only through the Qt main thread;
    on a headless run with no Qt application, `on_main_thread` raises
    `RuntimeError` and *write* runs directly instead. `LOG_DISPATCH_TIMEOUT`
    is 5s rather than `Mod.bridge.main_thread.DISPATCH_TIMEOUT`'s 60s, because
    a log line fires far more often than a Document mutation and cannot hold
    up the main thread that long. Any exception the dispatch raises,
    including a timeout while the panel is already shutting down, is
    swallowed rather than logged, since writing it risks the same off-thread
    crash this function exists to prevent.
    """
    from bridge.main_thread import on_main_thread

    try:
        on_main_thread(lambda: write(msg), timeout=LOG_DISPATCH_TIMEOUT)
    except RuntimeError:
        write(msg)
    except Exception:
        pass  # except-pass-allowed: no channel left to report the failure to


def _setup_session_log() -> str:
    """Create the per-session log directory/file and return its path."""
    try:
        mod_dir = FreeCAD.getUserAppDataDir() + "Mod/Daedalus/"
        log_dir = os.path.join(mod_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file  = os.path.join(log_dir, f"addon_{timestamp}.log")

        existing = sorted(
            [f for f in os.listdir(log_dir) if f.startswith("addon_") and f.endswith(".log")]
        )
        while len(existing) >= MAX_LOG_FILES:
            os.remove(os.path.join(log_dir, existing.pop(0)))

        return log_file
    except Exception as e:
        _console(FreeCAD.Console.PrintError, f"[Daedalus] Could not create session log: {e}\n")
        return ""


_session_log_file = _setup_session_log()


class _FreeCADHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        if record.levelno >= logging.ERROR:
            _console(FreeCAD.Console.PrintError, f"{msg}\n")
        else:
            _console(FreeCAD.Console.PrintMessage, f"{msg}\n")


def get_logger(name: str = "Daedalus") -> logging.Logger:
    lg = logging.getLogger(name)
    if lg.handlers:
        return lg

    lg.setLevel(logging.DEBUG)

    ch = _FreeCADHandler()
    ch.setFormatter(logging.Formatter("[Daedalus %(levelname)s] %(message)s"))
    lg.addHandler(ch)

    if _session_log_file:
        try:
            fh = logging.FileHandler(_session_log_file, encoding="utf-8")
            fh.setFormatter(
                logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")
            )
            lg.addHandler(fh)
        except Exception as e:
            _console(FreeCAD.Console.PrintError, f"[Daedalus] Could not attach file handler: {e}\n")

    return lg


logger: logging.Logger = get_logger()


def log_exception(exc: Exception, context: str = "") -> None:
    """
    Log a full stack trace to both the session file and the FreeCAD console.
    Call from any except-block: log_exception(e, "while invoking LLM")
    """
    tb = traceback.format_exc()
    header = f"Exception in {context}: {type(exc).__name__}: {exc}" if context else \
             f"{type(exc).__name__}: {exc}"
    logger.error(f"{header}\n{tb}")


def generate_crash_report(exc: Exception, context: str = "") -> str:
    """
    Build a human-readable crash report string (for display in the chat bubble).
    Includes OS, Python version, FreeCAD version, and a short traceback.
    """
    tb_lines = traceback.format_exc().strip().splitlines()
    short_tb = "\n".join(tb_lines[-10:])

    try:
        fc_ver = FreeCAD.Version()
        fc_str = f"{fc_ver[0]}.{fc_ver[1]}.{fc_ver[2]}"
    except Exception:
        fc_str = "unknown"

    report = (
        f"🚨 Crash Report\n"
        f"{'─' * 40}\n"
        f"Context : {context or 'unknown'}\n"
        f"Error   : {type(exc).__name__}: {exc}\n"
        f"OS      : {platform.system()} {platform.release()}\n"
        f"Python  : {sys.version.split()[0]}\n"
        f"FreeCAD : {fc_str}\n"
        f"Log     : {os.path.basename(_session_log_file)}\n"
        f"{'─' * 40}\n"
        f"{short_tb}"
    )

    log_exception(exc, context)
    return report


def notify_user(message: str) -> None:
    """
    Raise FreeCAD's own Notification Area for something a person, not a
    developer, needs to see -- a run stopped on a question and nobody is
    looking at the panel.

    `PrintUserWarning` is `Base::IntendedRecipient::User`, which the
    Notification Area shows as a pop-up bubble by default; the addon's own
    `PrintWarning`/`PrintError` calls above are `Developer`-recipient and are
    suppressed there unless the user has opted in under Preferences, so they
    are not a substitute (FreeCAD 1.1 `src/Gui/NotificationArea.cpp`,
    `NotificationAreaObserver::sendLog`). Routed through `_console` for the
    same reason every other call here is: built off the GUI thread, the
    Notification Area's own widgets crash FreeCAD (see the module docstring).
    """
    _console(FreeCAD.Console.PrintUserWarning, message)

