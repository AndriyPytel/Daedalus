"""
The gate questions must never be able to hang the process.

`AskTheUser` blocks the asking thread on an Event that only an answer releases.
Asked from the thread that owns the object, with nothing connected to answer
inline, that Event can never be set: the queued answer needs an event loop the
asking thread is blocked out of. The whole run stops there, at no CPU and with
no traceback, which is how an overnight suite disappears.
"""
import pytest

from tests.conftest import HAVE_QT, NO_QT, QT_APP  # noqa: F401

pytestmark = pytest.mark.skipif(not HAVE_QT, reason=NO_QT)


def _asks():
    from ui.daedalus_panel import AskTheUser

    return AskTheUser()


def test_a_question_nobody_can_answer_is_refused_rather_than_waited_on():
    asks = _asks()
    with pytest.raises(RuntimeError) as refused:
        asks.about_the_plan(["a step"])
    assert "answer" in str(refused.value)


def test_an_answer_given_inline_still_returns_it():
    asks = _asks()
    asks.plan_gate.connect(lambda steps: asks.answer("yes"))
    assert asks.about_the_plan(["a step"]) == "yes"


def test_every_gate_question_is_covered_by_the_refusal():
    asks = _asks()
    for ask, payload in (
        (asks.about_the_plan, ["a step"]),
        (asks.about_objects_we_do_not_own, ["Box"]),
        (asks.about_missing_values, "how wide?"),
        (asks.about_the_revision, "it did not pass"),
    ):
        with pytest.raises(RuntimeError):
            ask(payload)
