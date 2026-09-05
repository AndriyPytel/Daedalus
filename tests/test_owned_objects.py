"""
Level 1 — the Owned Object rule, in both directions.

An Owned Object is one the assistant created during this Session. Anything else
is the user's, including everything that existed before the assistant did, and
may not be deleted without asking. Modifying it is covered by the Plan Gate: the
user read and approved the Step that says so.

What a Step touches is taken from Preflight, which actually ran it, rather than
from reading the code.

The Owned Object rule asks about the user's objects. It has nothing to say about
the assistant's own, and a run asked to amend an existing part cleared every
feature the earlier phase of the same Session had built and started again. What
it threw away was a finished part the user had watched being made, so the size
of the deletion is its own question.
"""
import pytest

from core.apply import ApplyPipeline, refuse_without_asking
from core.kernel import KernelResult
from tests.test_apply_pipeline import FakeDocument, FakeObject, FakeWorker


class Asked:
    """Records what the user was asked, answers however the test says."""

    def __init__(self, answer=True):
        self.answer = answer
        self.asks = []

    def __call__(self, kinds):
        self.asks.append({key: list(value) for key, value in kinds.items()})
        return self.answer


def pipeline(worker, confirm=None, owned=None, created=("Box",), events=None):
    doc = FakeDocument()

    def transaction(document, code, label, **_):
        for name in created:
            document.Objects.append(FakeObject(name))

    built = ApplyPipeline(document=doc, worker=worker,
                          dispatch=lambda func, timeout=None: func(),
                          transaction=transaction, confirm=confirm, owned=owned,
                          events=events)
    built.document = doc
    return built


def touching(modified=(), deleted=(), created=()):
    return FakeWorker(KernelResult(ok=True, render="/tmp/r.png",
                                      modified=list(modified), deleted=list(deleted),
                                      created=list(created)))


def test_creating_something_new_is_never_confirmed():
    asked = Asked()

    result = pipeline(touching(created=["Box"]), confirm=asked).apply("box()")

    assert result.ok
    assert asked.asks == []


def test_modifying_an_object_we_created_is_not_confirmed():
    """The first apply creates the Box, which makes it the assistant's own."""
    asked = Asked()
    built = pipeline(touching(created=["Box"]), confirm=asked)
    built.apply("box()")

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
    modifying the user's object, and must not turn into a prompt. The worker
    already filtered Shape-only changes, so `modified` is empty.
    """
    asked = Asked()

    result = pipeline(touching(created=["Fillet"]), confirm=asked).apply("fillet()")

    assert result.ok
    assert asked.asks == []


def test_modifying_an_object_the_user_made_is_covered_by_the_plan_gate():
    """
    The Plan the user approved named the change in prose. A second question per
    Step is asking twice for the same consent.
    """
    asked = Asked(answer=True)

    result = pipeline(touching(modified=["UserBox"]), confirm=asked).apply("user_box()")

    assert result.ok
    assert asked.asks == []


def test_deleting_an_object_the_user_made_asks_first():
    asked = Asked(answer=True)

    result = pipeline(touching(deleted=["UserBox"]), confirm=asked).apply("remove()")

    assert result.ok
    assert asked.asks == [{"delete": ["UserBox"]}]


def test_saying_no_stops_the_step_and_says_why_to_the_assistant():
    asked = Asked(answer=False)
    built = pipeline(touching(deleted=["UserBox"]), confirm=asked)

    result = built.apply("doc.removeObject('UserBox')")

    assert not result.ok
    assert "you created rather than I did" in result.traceback
    assert "delete UserBox" in result.traceback
    assert built.document.Objects == [], "the Step ran anyway"


def test_the_refusal_tells_the_assistant_what_to_do_instead():
    result = pipeline(touching(deleted=["UserBox"]), confirm=Asked(answer=False)).apply("rm()")

    assert "without deleting the user's own objects" in result.traceback
    assert "say why it cannot be done and stop" in result.traceback


def test_a_step_that_modifies_and_deletes_asks_about_the_deletion_only():
    asked = Asked(answer=False)

    result = pipeline(touching(modified=["A"], deleted=["B"]), confirm=asked).apply("x()")

    assert asked.asks == [{"delete": ["B"]}]
    assert "delete B" in result.traceback
    assert "modify" not in result.traceback


def test_only_the_objects_that_are_not_ours_are_named():
    asked = Asked(answer=True)
    built = pipeline(touching(created=["Ours"]), confirm=asked, created=("Ours",))
    built.apply("make_ours()")

    built._worker = touching(deleted=["Ours", "Theirs"])
    built.apply("remove_both()")

    assert asked.asks == [{"delete": ["Theirs"]}]


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
    worker = FakeWorker(KernelResult(ok=False, traceback="boom"))

    result = pipeline(worker, confirm=asked).apply("x()")

    assert not result.ok
    assert asked.asks == []


def test_with_no_way_to_ask_the_users_work_is_left_alone():
    result = pipeline(touching(deleted=["UserBox"])).apply("x()")

    assert not result.ok, "deleted the user's object with nobody to ask"


def test_the_default_answer_is_no():
    assert refuse_without_asking({"delete": ["UserBox"]}) is False


def test_with_no_way_to_ask_our_own_objects_are_still_fine():
    built = pipeline(touching(created=["Box"]))

    assert built.apply("box()").ok


def test_asking_to_delete_the_users_own_work_is_on_the_stream():
    """
    This is the one question the user is asked that does not go through the
    loop, so it was the one that never reached the Session log. A Document that
    lost an object with nothing anywhere saying it was allowed is the failure.
    """
    from core.events import ANSWER, QUESTION, EventStream

    events = EventStream()
    built = pipeline(touching(deleted=["UserBox"]), confirm=Asked(True), events=events)

    assert built.apply("delete()").ok

    assert [event.kind for event in events] == [QUESTION, ANSWER]
    assert "UserBox" in events.first(QUESTION).data["text"]
    assert events.first(ANSWER).data["text"] == "You allowed it to delete UserBox."
    assert events.first(ANSWER).data["by"] == "user", \
        "a person answered it, so a replay must not read it as an assumption"


def test_refusing_is_recorded_just_as_plainly():
    from core.events import ANSWER, EventStream

    events = EventStream()
    built = pipeline(touching(deleted=["UserBox"]), confirm=Asked(False), events=events)

    assert not built.apply("delete()").ok
    assert events.first(ANSWER).data["text"] == "You refused to let it delete UserBox."


def test_a_step_that_deletes_nothing_of_theirs_asks_nothing_and_says_nothing():
    from core.events import EventStream

    events = EventStream()
    built = pipeline(touching(created=["Box"]), confirm=Asked(), events=events)

    assert built.apply("box()").ok
    assert list(events) == [], "a question nobody was asked is noise in the transcript"


def test_the_pipeline_narrates_nothing_when_no_run_is_listening():
    """`tools/run_slice.py` and the Level 2 harness build one with no stream."""
    built = pipeline(touching(deleted=["UserBox"]), confirm=Asked(True))

    assert built.apply("delete()").ok


def test_only_an_explicit_yes_is_consent():
    """
    Stop releases every waiting question with the Plan Gate's answer, which is
    an object and therefore truthy. Anything that is not a yes must not let a
    Step delete the user's own work.
    """
    for answer in (None, "", object(), refuse_without_asking):
        built = pipeline(touching(deleted=["UserBox"]), confirm=lambda kinds: answer)

        assert not built.apply("delete()").ok, "%r was taken for consent" % (answer,)


class Feature(FakeObject):
    """A PartDesign feature, which is a piece of the model."""

    def __init__(self, name, type_id="PartDesign::Pad", derives=("PartDesign::Feature",)):
        super().__init__(name)
        self.TypeId = type_id
        self._derives = derives

    def isDerivedFrom(self, kind):
        return kind in self._derives or kind == self.TypeId


class Sketch(Feature):
    def __init__(self, name):
        super().__init__(name, "Sketcher::SketchObject", ("Part::Part2DObject",))


class Tree(FakeDocument):
    """A Document that can be asked what an object is, as a real one can."""

    def __init__(self, objects=()):
        super().__init__()
        self.Objects = list(objects)

    def getObject(self, name):
        return next((o for o in self.Objects if o.Name == name), None)


def modelled(worker, objects, confirm=None, owned=None, events=None):
    doc = Tree(objects)

    def transaction(document, code, label, **_):
        pass

    return ApplyPipeline(document=doc, worker=worker,
                         dispatch=lambda func, timeout=None: func(),
                         transaction=transaction, confirm=confirm,
                         owned=owned, events=events)


THE_TREE = [Sketch("SketchOuterProfile"), Feature("PadOuterProfile"),
            Sketch("SketchBoreLeft"), Feature("PocketBoreLeft", "PartDesign::Pocket"),
            Sketch("SketchBoreRight"), Feature("PocketBoreRight", "PartDesign::Pocket"),
            Feature("FilletRedEdges", "PartDesign::Fillet")]

EVERYTHING = [o.Name for o in THE_TREE]

OURS = set(EVERYTHING)


def test_clearing_the_feature_tree_asks_even_when_the_assistant_built_it():
    asked = Asked(answer=True)

    result = modelled(touching(deleted=EVERYTHING), THE_TREE,
                      confirm=asked, owned=OURS).apply("wipe()")

    assert result.ok
    assert asked.asks, "the assistant deleted the whole model without asking"
    assert asked.asks[-1] == {"rebuild": ["FilletRedEdges", "PadOuterProfile",
                                          "PocketBoreLeft", "PocketBoreRight"]}


def test_refusing_the_rebuild_stops_the_step_and_says_edit_instead():
    result = modelled(touching(deleted=EVERYTHING), THE_TREE,
                      confirm=Asked(answer=False), owned=OURS).apply("wipe()")

    assert not result.ok
    assert "rebuild of a model that is already there" in result.traceback
    assert "Edit the features that exist" in result.traceback
    assert "add a feature to the end of it" in result.traceback


def test_replacing_one_feature_and_its_sketch_is_a_correction_not_a_rebuild():
    """
    A Pad padded the wrong way comes out with the sketch it was drawn on. That
    is one change to the part, and asking about it is asking the user to
    approve every ordinary correction.
    """
    asked = Asked(answer=True)

    result = modelled(touching(deleted=["SketchBoreLeft", "PocketBoreLeft"]), THE_TREE,
                      confirm=asked, owned=OURS).apply("redo_the_bore()")

    assert result.ok
    assert asked.asks == []


def test_deleting_sketches_alone_is_not_counted_as_a_rebuild():
    asked = Asked(answer=True)

    modelled(touching(deleted=["SketchBoreLeft", "SketchBoreRight"]), THE_TREE,
             confirm=asked, owned=OURS).apply("tidy()")

    assert asked.asks == []


def test_deleting_the_body_is_a_rebuild_by_itself():
    """One name from Preflight, every feature in the Document gone with it."""
    body = Feature("Body", "PartDesign::Body", ("PartDesign::Body",))
    asked = Asked(answer=False)

    result = modelled(touching(deleted=["Body", "PadOuterProfile"]), THE_TREE + [body],
                      confirm=asked, owned=OURS | {"Body"}).apply("del_body()")

    assert not result.ok
    assert asked.asks == [{"rebuild": ["Body", "PadOuterProfile"]}]


def test_measurements_and_scratch_objects_do_not_count_towards_a_rebuild():
    asked = Asked(answer=True)
    scratch = [FakeObject("Length"), FakeObject("Measurements"),
               FakeObject("Phase1CutoutReference")]
    doc = THE_TREE + scratch
    ours = OURS | {"Length", "Measurements", "Phase1CutoutReference"}

    modelled(touching(deleted=["Length", "Measurements", "Phase1CutoutReference"]), doc,
             confirm=asked, owned=ours).apply("tidy()")

    assert asked.asks == [], "a Measurement is not a piece of the model"


def test_the_rebuild_question_is_on_the_stream_like_every_other():
    from core.events import ANSWER, QUESTION, EventStream

    events = EventStream()
    built = modelled(touching(deleted=EVERYTHING), THE_TREE,
                     confirm=Asked(True), owned=OURS, events=events)

    assert built.apply("wipe()").ok

    assert [event.kind for event in events] == [QUESTION, ANSWER]
    assert "4 features of the existing model" in events.first(QUESTION).data["text"]
    assert events.first(ANSWER).data["by"] == "user"


def test_a_rebuild_with_nobody_to_ask_is_refused():
    result = modelled(touching(deleted=EVERYTHING), THE_TREE, owned=OURS).apply("wipe()")

    assert not result.ok, "wiped the model with nobody to ask"


def test_both_questions_are_asked_when_a_step_does_both():
    """The user's own object, and the model — two different reasons to stop."""
    theirs = Feature("UserPad")
    asked = Asked(answer=True)

    result = modelled(touching(deleted=EVERYTHING + ["UserPad"]), THE_TREE + [theirs],
                      confirm=asked, owned=OURS).apply("wipe()")

    assert result.ok
    assert asked.asks == [{"delete": ["UserPad"]},
                          {"rebuild": ["FilletRedEdges", "PadOuterProfile", "PocketBoreLeft",
                                       "PocketBoreRight", "UserPad"]}]


def test_refusing_the_first_question_never_reaches_the_second():
    asked = Asked(answer=False)
    theirs = Feature("UserPad")

    result = modelled(touching(deleted=EVERYTHING + ["UserPad"]), THE_TREE + [theirs],
                      confirm=asked, owned=OURS).apply("wipe()")

    assert not result.ok
    assert asked.asks == [{"delete": ["UserPad"]}]
    assert "you created rather than I did" in result.traceback
