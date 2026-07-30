# FreeCadCopilot/utils/logger.py
"""
Session-aware logging for FreeCAD Copilot.

Features
--------
- One timestamped log file per FreeCAD session  (addon_YYYYMMDD_HHMMSS.log)
- Keeps the latest MAX_LOG_FILES session files; deletes older ones on startup
- log_exception(exc, context)   – writes full stack trace to file + console
- generate_crash_report(exc)    – summarised crash string for the chat UI
"""
import FreeCAD
import logging
import os
import sys
import platform
import traceback
from datetime import datetime

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_LOG_FILES = 10   # rotating window of session files to keep

# ── Session log file (created once at import time) ────────────────────────────
_session_log_file: str = ""


def _setup_session_log() -> str:
    """Create the per-session log directory/file and return its path."""
    try:
        mod_dir = FreeCAD.getUserAppDataDir() + "Mod/FreeCadCopilot/"
        log_dir = os.path.join(mod_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file  = os.path.join(log_dir, f"addon_{timestamp}.log")

        # Rotate – delete oldest files beyond the limit
        existing = sorted(
            [f for f in os.listdir(log_dir) if f.startswith("addon_") and f.endswith(".log")]
        )
        while len(existing) >= MAX_LOG_FILES:
            os.remove(os.path.join(log_dir, existing.pop(0)))

        return log_file
    except Exception as e:
        FreeCAD.Console.PrintError(f"[Copilot] Could not create session log: {e}\n")
        return ""


_session_log_file = _setup_session_log()


# ── FreeCAD console handler ───────────────────────────────────────────────────

class _FreeCADHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        if record.levelno >= logging.ERROR:
            FreeCAD.Console.PrintError(f"{msg}\n")
        else:
            FreeCAD.Console.PrintMessage(f"{msg}\n")


# ── Logger factory ────────────────────────────────────────────────────────────

def get_logger(name: str = "FreeCadCopilot") -> logging.Logger:
    lg = logging.getLogger(name)
    if lg.handlers:
        return lg

    lg.setLevel(logging.DEBUG)

    # Console handler
    ch = _FreeCADHandler()
    ch.setFormatter(logging.Formatter("[Copilot %(levelname)s] %(message)s"))
    lg.addHandler(ch)

    # File handler (session file)
    if _session_log_file:
        try:
            fh = logging.FileHandler(_session_log_file, encoding="utf-8")
            fh.setFormatter(
                logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")
            )
            lg.addHandler(fh)
        except Exception as e:
            FreeCAD.Console.PrintError(f"[Copilot] Could not attach file handler: {e}\n")

    return lg


logger: logging.Logger = get_logger()


# ── Public helpers ────────────────────────────────────────────────────────────

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
    # keep last 10 lines to stay concise
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

    # Also write full trace to session file
    log_exception(exc, context)
    return report


def get_session_log_path() -> str:
    """Return the path of the active session log file."""
    return _session_log_file
