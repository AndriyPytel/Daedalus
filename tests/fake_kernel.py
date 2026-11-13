"""A Kernel stand-in for Level 1: the five verbs, answering as the test says and remembering what it was asked."""
from core.kernel import KernelResult


class FakeKernel:
    def __init__(self, rehearsal=None, probing=None, drawing=None, facts=None):
        self.rehearsal = rehearsal or KernelResult(ok=True, created=["Box"])
        self.probing = probing or KernelResult(ok=True)
        self.drawing = drawing or KernelResult(ok=True, render=None)
        self.facts = facts
        self.rehearsed, self.probed, self.seeded, self.rendered, self.measured = [], [], [], [], []

    def rehearse(self, code, timeout=None):
        self.rehearsed.append(code)
        return self.rehearsal

    def probe(self, code, timeout=None):
        self.probed.append(code)
        return self.probing

    def seed(self, path=None):
        self.seeded.append(path)
        return KernelResult(ok=True)

    def render(self, path, view=None):
        self.rendered.append((path, view))
        return self.drawing

    def measure(self, script, timeout=None):
        self.measured.append((script, timeout))
        return next(self.facts) if hasattr(self.facts, "__next__") else self.facts
