"""A Kernel stand-in for Level 1: the five verbs, answering as the test says and remembering what it was asked."""
from core.kernel import KernelResult


class FakeKernel:
    def __init__(self, rehearsal=None, drawing=None, facts=None):
        self.rehearsal = rehearsal or KernelResult(ok=True, created=["Box"])
        self.drawing = drawing or KernelResult(ok=True, render=None)
        self.facts = facts
        self.rehearsed, self.seeded, self.rules, self.measured = [], [], [], []

    def rehearse(self, code, timeout=None):
        self.rehearsed.append(code)
        return self.rehearsal

    def probe(self, code, timeout=None):
        return KernelResult(ok=True)

    def seed(self, path=None, rules=None):
        self.seeded.append(path)
        self.rules.append(rules)
        return self.rehearsal

    def render(self, path, view=None):
        return self.drawing

    def measure(self, script, timeout=None):
        self.measured.append((script, timeout))
        return next(self.facts) if hasattr(self.facts, "__next__") else self.facts
