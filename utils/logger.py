"""
Session-aware logging for FreeCAD Copilot.

Features
--------
- One timestamped log file per FreeCAD session  (addon_YYYYMMDD_HHMMSS.log)
- Keeps the latest MAX_LOG_FILES session files; deletes older ones on startup
- log_exception(exc, context)   – writes full stack trace to file + console
- generate_crash_report(exc)    – summarised crash string for the chat UI

Every `FreeCAD.Console` write in this module is marshalled onto the Qt main
thread. `LoopWorker.do_work` (`ui/copilot_panel.py`) runs on a QThread and
logs an ERROR event from it; a run that hit `ToolError: There is no open
Document` printed the line, then:

    QObject::setParent: Cannot set parent, new parent is in a different thread   <- x16
    QObject::killTimer: Timers cannot be stopped from another thread
    zsh: segmentation fault  /Applications/FreeCAD.app/Contents/MacOS/FreeCAD

FreeCAD's console observers are Qt: the Report View and, on 1.0+, the
Notification Area, which builds a widget tree and an auto-hide QTimer per
message. Built on a non-GUI thread, that is the sixteen `setParent` warnings
(one per widget in the tree) and the `killTimer` warning, and then the
segfault. The `FileHandler` needs none of this — it is plain file I/O, no Qt
object involved — so only the two `FreeCAD.Console` routes below are guarded.
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
    Call a `FreeCAD.Console.Print*` method without ever running it off the
    Qt main thread — see the module docstring for what happens if it does.

    `on_main_thread` (`core.apply`) raises `RuntimeError` when there is no
    Qt application at all — Level 1, headless, `freecadcmd` — and there is no
    GUI there to corrupt, so *write* just runs directly.

    Imported locally: `core.apply` imports `core.freecad_tools`, which needs
    `on_main_thread` too and already imports it locally for the same reason,
    to keep this module out of that cycle.

    LOG_DISPATCH_TIMEOUT is 5s, not the 60s `core.apply.DISPATCH_TIMEOUT`
    gives a Document mutation: a log line is one `PrintError` call, not a
    boolean operation, it fires far more often than a Step, and blocking it
    for a minute would make logging itself look like the hang it exists to
    report.

    A line the main thread never comes back for is dropped rather than
    written from here. `CopilotPanel.shut_down` waits on the worker thread
    from the main one, and the worker emits `blocked` on its way out of a
    Stop — so the one moment the marshalling cannot complete is the one
    moment a line is most likely to be logged, and writing it anyway is the
    segfault this exists to prevent. The `FileHandler` already has it.

    Everything else the dispatch can raise is dropped for the same reason,
    and dropped here rather than at `_FreeCADHandler.emit`: `on_main_thread`
    re-raises whatever `write` raised on the main thread verbatim, and `_qt`
    raises `ImportError` when neither PySide binding resolves. Neither is a
    `RuntimeError` or a `TimeoutError`, `logging.Handler.emit` is not called
    inside a try in `Logger.callHandlers`, and the one `logger.error` this
    addon makes outside `EventStream.emit`'s own guard is `log_exception`,
    reached from `generate_crash_report` while `LoopWorker.do_work` is already
    handling a crash — so an escape there loses `finished`, and the panel
    never learns the run ended.
    """
    from core.apply import on_main_thread

    try:
        on_main_thread(lambda: write(msg), timeout=LOG_DISPATCH_TIMEOUT)
    except RuntimeError:
        write(msg)
    except Exception:
        pass


def _setup_session_log() -> str:
    """Create the per-session log directory/file and return its path."""
    try:
        mod_dir = FreeCAD.getUserAppDataDir() + "Mod/FreeCadCopilot/"
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
        _console(FreeCAD.Console.PrintError, f"[Copilot] Could not create session log: {e}\n")
        return ""


_session_log_file = _setup_session_log()


class _FreeCADHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        if record.levelno >= logging.ERROR:
            _console(FreeCAD.Console.PrintError, f"{msg}\n")
        else:
            _console(FreeCAD.Console.PrintMessage, f"{msg}\n")


def get_logger(name: str = "FreeCadCopilot") -> logging.Logger:
    lg = logging.getLogger(name)
    if lg.handlers:
        return lg

    lg.setLevel(logging.DEBUG)

    ch = _FreeCADHandler()
    ch.setFormatter(logging.Formatter("[Copilot %(levelname)s] %(message)s"))
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
            _console(FreeCAD.Console.PrintError, f"[Copilot] Could not attach file handler: {e}\n")

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

