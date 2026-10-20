"""
`ApplyPipeline.apply(kernel_result=None)` is a Step nothing rehearsed: the
gates that read the kernel's findings have nothing to read, and the names the
transaction allocated are kept rather than replaced by the kernel's.
"""
from core.freecad_apply import ApplyPipeline


class Doc:
    def __init__(self):
        self.Objects = []


def _pipeline(allocate=None):
    doc = Doc()
    landed = []

    def transaction(document, code, label, names=None, **_):
        landed.append(code)
        if allocate:
            names.update(allocate)

    built = ApplyPipeline(document=doc, worker=None, dispatch=lambda f, timeout=None: f(),
                          transaction=transaction)
    built.landed = landed
    return built


def test_a_step_with_no_kernel_result_lands_where_an_empty_one_is_refused(monkeypatch):
    monkeypatch.setattr("core.freecad_apply._show_tip", lambda doc: None)
    monkeypatch.setattr("core.freecad_apply.fit_view", lambda doc: None)
    monkeypatch.setattr("core.freecad_apply.description", lambda doc: {"features": []})

    built = _pipeline()
    assert built.apply("x = 1", kernel_result=None).ok
    assert built.landed == ["x = 1"]

    refused = _pipeline().apply("x = 1", kernel_result={})
    assert refused.gate == "changed_nothing"


def test_lint_still_runs_without_a_kernel_result():
    built = _pipeline()
    result = built.apply("fillet.Base = (box, ['Edge12'])", kernel_result=None)

    assert result.gate == "lint"
    assert built.landed == []


def test_the_names_a_step_allocated_survive_it(monkeypatch):
    monkeypatch.setattr("core.freecad_apply._show_tip", lambda doc: None)
    monkeypatch.setattr("core.freecad_apply.fit_view", lambda doc: None)
    monkeypatch.setattr("core.freecad_apply.description", lambda doc: {"features": []})

    built = _pipeline(allocate={"Box": "Box001"})
    assert built.apply("x = 1", kernel_result=None).ok

    assert built._names == {"Box": "Box001"}
