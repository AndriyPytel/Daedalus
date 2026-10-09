"""
A Trap: a claim about FreeCAD's behaviour and, where one can be written, the script that reproduces it.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Trap:
    claim: str
    script: str = None
