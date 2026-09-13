"""
Where this checkout's own logs live -- one computation every log path
derives from. Resolved from this file's own location, never the process's
current working directory, so a run started from another directory does not
scatter output elsewhere; two call sites once did this arithmetic
themselves and drifted apart into separate log directories.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.path.join(ROOT, "logs")
