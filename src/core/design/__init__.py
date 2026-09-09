"""core.design package: re-exports core.design.design's public names so a caller
outside core/ reaches them as `from core.design import SECTIONS`."""
from core.design.design import *  # noqa: F401,F403
