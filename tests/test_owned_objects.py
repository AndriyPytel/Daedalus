"""
Level 1 — the Owned Object rule, in both directions.

An Owned Object is one the assistant created during this Session. Anything else
is the user's, including everything that existed before the assistant did, and
may not be modified or deleted without asking — regardless of what the Plan Gate
already approved.

What a Step touches is taken from Preflight, which actually ran it, rather than
from reading the code.
"""
import pytest

from core.apply import ApplyPipeline, refuse_without_asking
from core.preflight import PreflightResult
from tests.test_apply_pipeline import FakeDocument, FakeObject, FakeWorker


class Asked:
    """Records what the user was asked, answers however the test says."""

    def __init__(self, answer=True):
        self.answer = answer
        self.asks = []

    def __call__(self, kinds):
        self.asks.append({key: list(value) for key, value in kinds.items()})
        return self.answer


def pipeline(worker, confirm=None, owned=None, created=("Box",)):
    doc = FakeDocument()

    def transaction(document, code, label):
        for name in created:
            document.Objects.append(FakeObject(name))

    built = ApplyPipeline(document=doc, worker=worker,
                          dispatch=lambda func, timeout=None: func(),
                          transaction=transaction, confirm=confirm, owned=owned)
    built.document = doc
    return built


def touching(modified=(), deleted=(), created=()):
    return FakeWorker(PreflightResult(ok=True, render="/tmp/r.png",
                                      modified=list(modified), deleted=list(deleted),
                                      created=list(created)))


# ── the direction where nothing is asked ──────────────────────────────────────


def test_creating_something_new_is_never_confirmed():
    asked = Asked()

    result = pipeline(touching(created=["Box"]), confirm=asked).apply("box()")

    assert result.ok
    assert asked.asks == []


def test_modifying_an_object_we_created_is_not_confirmed():
    asked = Asked()
    built = pipeline(touching(created=["Box"]), confirm=asked)
    built.apply("box()")                      # now Box is ours

    result = built.apply("box.Length = 20")

    assert result.ok
    assert asked.asks == [], "asked about an object the assistant made itself"


def test_deleting_an_object_we_created_is_not_confirmed():
    asked = Asked()
    built = pipeline(touching(created=["Box"]), confirm=asked)
    built.apply("box()")

    built._worker = touching(deleted=["Box"])
    result = built.apply("doc.removeObject('Box')")

    assert result.ok
    assert asked.asks == []


def test_ownership_is_remembered_across_steps_in_a_session():
    built = pipeline(touching(created=["Box"]), confirm=Asked(), created=("Box", "Pad"))
    built.apply("box()")

    assert built.owned == {"Box", "Pad"}


def test_recompute_of_a_dependent_object_is_not_a_modification():
    """
    Preflight reports what changed; a downstream Shape recompute is not the Step
    modifying the user's object, and must not turn into a prompt.
    """
    asked = Asked()

    # The worker already filtered Shape-only changes, so `modified` is empty.
    result = pipeline(touching(created=["Fillet"]), confirm=asked).apply("fillet()")

    assert result.ok
    assert asked.asks == []


# ── the direction where the user is asked ─────────────────────────────────────


def test_modifying_an_object_the_user_made_asks_first():
    asked = Asked(answer=True)

    result = pipeline(touching(modified=["UserBox"]), confirm=asked).apply("user_box()")

    assert result.ok
    assert asked.asks == [{"modify": ["UserBox"], "delete": []}]


def test_deleting_an_object_the_user_made_asks_first():
    asked = Asked(answer=True)

    result = pipeline(touching(deleted=["UserBox"]), confirm=asked).apply("remove()")

    assert result.ok
    assert asked.asks == [{"modify": [], "delete": ["UserBox"]}]


def test_saying_no_stops_the_step_and_says_why_to_the_assistant():
    asked = Asked(answer=False)
    built = pipeline(touching(modified=["UserBox"]), confirm=asked)

    result = built.apply("user_box.Length = 5")

    assert not result.ok
    assert "you created rather than I did" in result.traceback
    assert "modify UserBox" in result.traceback
    assert built.document.Objects == [], "the Step ran anyway"


def test_the_refusal_tells_the_assistant_what_to_do_instead():
    result = pipeline(touching(deleted=["UserBox"]), confirm=Asked(answer=False)).apply("rm()")

    assert "without changing the user's own objects" in result.traceback
    assert "say why it cannot be done and stop" in result.traceback


def test_both_kinds_are_reported_in_one_question():
    asked = Asked(answer=True)

    pipeline(touching(modified=["A"], deleted=["B"]), confirm=asked).apply("x()")

    assert asked.asks == [{"modify": ["A"], "delete": ["B"]}]


def test_only_the_objects_that_are_not_ours_are_named():
    asked = Asked(answer=True)
    built = pipeline(touching(created=["Ours"]), confirm=asked, created=("Ours",))
    built.apply("make_ours()")

    built._worker = touching(modified=["Ours", "Theirs"])
    built.apply("touch_both()")

    assert asked.asks == [{"modify": ["Theirs"], "delete": []}]


# ── the rule beats the Plan Gate ──────────────────────────────────────────────


def test_the_rule_applies_regardless_of_plan_gate_approval():
    """
    The pipeline is never told whether the Plan was approved — approval cannot
    reach it, which is the structural version of "regardless of the Plan Gate".
    """
    import inspect

    signature = inspect.signature(ApplyPipeline.apply)

    assert "approved" not in signature.parameters
    assert "gate" not in signature.parameters


def test_preflight_still_runs_before_the_question_is_asked():
    """Asking about what a Step 'might' do would mean guessing; Preflight knows."""
    asked = Asked(answer=True)
    worker = touching(modified=["UserBox"])

    pipeline(worker, confirm=asked).apply("x()")

    assert worker.checked, "the user was asked before the Step was even Preflighted"


def test_a_failed_preflight_never_reaches_the_question():
    asked = Asked(answer=True)
    worker = FakeWorker(PreflightResult(ok=False, traceback="boom"))

    result = pipeline(worker, confirm=asked).apply("x()")

    assert not result.ok
    assert asked.asks == []


# ── failing closed ────────────────────────────────────────────────────────────


def test_with_no_way_to_ask_the_users_work_is_left_alone():
    result = pipeline(touching(modified=["UserBox"])).apply("x()")

    assert not result.ok, "modified the user's object with nobody to ask"


def test_the_default_answer_is_no():
    assert refuse_without_asking({"modify": ["UserBox"], "delete": []}) is False


def test_with_no_way_to_ask_our_own_objects_are_still_fine():
    built = pipeline(touching(created=["Box"]))

    assert built.apply("box()").ok
