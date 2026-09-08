"""core.kernel package: re-exports core.kernel.kernel's public names so
`from core.kernel import Kernel` keeps working for callers outside core/."""
from core.kernel.kernel import *  # noqa: F401,F403
