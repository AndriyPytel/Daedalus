"""Where this checkout's own logs live -- one computation every log path derives from.

Resolved from this file's own location, never the process's current working
directory: a run started from another directory must not scatter output
elsewhere. `src/logs/` and `src/core/logs/` existed only because two call
sites each did this arithmetic themselves and drifted apart -- FCC-358.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.path.join(ROOT, "logs")
