"""
The one assembly the addon panel and an external caller both use.

`Client` owns a Session (`core.llm.Session`, FCC-309/FCC-310), a Kernel
(FCC-296) and a Document, and turns a prompt into whichever of Design, a run
question, or a control instruction the routing orchestrator (FCC-313) names.
A script drives it exactly as `ui/copilot_panel.py`'s Qt panel will once
FCC-317 repoints the panel onto this same object -- no Qt application, no
`FreeCADGui`, because `ApplyPipeline`'s own `dispatch` and `confirm` are
plain callables here rather than the main-thread marshaller a GUI needs
(`core/apply.py :: on_main_thread`).

This is what `run.py` and `lane.py`, the two surviving scripts in the
harness directory, drive to start a run, replacing the hand-assembled
scripts they took over from: both a second copy of this same wiring, one
call behind the loop's own `run()` signature.
"""
from core import events
from core.freecad_apply import ApplyPipeline, active_document, build_registry, closing
from core.events import EventStream, replay
from core.kernel import Kernel
from core.llm import (EFFORTS, PROFILES, TEXT, Session, client_from_roles,
                      effort_for_profile, effort_is_honoured, selected_model_name,
                      unusable_attachments)
from core.loop import (ACCEPT, APPROVE, DECISIONS, REJECT, REVISE, START_OVER,
                       Approval, NeedsAnswer, auto_approve, revise_by_default, run)
from core.narrate import narrate
from core.orchestrator import CONTROL, DESIGN, RUN_QUESTION, route
from core.session_title import fallback, generate
from core.sessions import Sessions


class _NoKernel:
    """Sentinel for Client(kernel=NO_KERNEL) to run in-process without a subprocess."""
    pass


NO_KERNEL = _NoKernel()


class Client:
    """
    *document* is a path to open, `None` for a fresh one, or an already-open
    FreeCAD Document object (live-document mode, so the panel binds to the
    Document it already has). *llm* and *kernel* default to a real provider
    and a real headless kernel; a test hands in its own. Pass *kernel* as
    `NO_KERNEL` to run in-process without a subprocess (the panel does this
    against the live GUI Document). Without an *answer* callback `send` pauses
    in a blocked, resumable `needs-answer` state rather than guessing, and
    *confirm* and *dispatch* set the corresponding `ApplyPipeline` callables.
    """

    def __init__(self, document=None, llm=None, kernel=None, answer=None,
                 confirm=None, dispatch=None):
        import FreeCAD

        if document is None:
            self.document = FreeCAD.newDocument()
        elif isinstance(document, str):
            self.document = FreeCAD.openDocument(document)
        else:
            self.document = document

        if isinstance(kernel, _NoKernel):
            self.kernel = None
        elif kernel is None:
            self.kernel = Kernel()
        else:
            self.kernel = kernel

        self._llm = llm
        self.events = EventStream()
        self.session = Session()
        self.pipeline = ApplyPipeline(
            document=self.document,
            worker=self.kernel,
            dispatch=dispatch or (lambda func, timeout=None: func()),
            confirm=confirm or (lambda kinds: False),
            events=self.events)
        self.answer = answer
        self._stop = False
        self._abort = False
        self._pending = None

    @property
    def llm(self):
        if self._llm is None:
            self._llm = client_from_roles()
        return self._llm

    @llm.setter
    def llm(self, value):
        self._llm = value

    def watch(self, callback):
        """Subscribe *callback* to every event a `send` emits, as it happens."""
        self.events.subscribe(callback)

    def send(self, prompt, gate=auto_approve, ask=None, decide=revise_by_default,
             attachments=(), cancelled=None, outstanding=(), the_part_path=None,
             route_first=True, events=None):
        """
        Run *prompt* through the loop, blocking until done. *route_first*
        classifies via the orchestrator first (default); `False` skips straight
        to design. *events* overrides this Client's stream for this run only.
        """
        stream = events if events is not None else self.events
        self.pipeline.events = stream

        self._stop = False
        self._abort = False
        tools = build_registry(document=self.document, pipeline=self.pipeline)

        def _pause(question):
            self._pending = dict(prompt=prompt, gate=gate, decide=decide,
                                 attachments=attachments)
            raise NeedsAnswer(question)

        answered_by = ask or self.answer or _pause

        def is_cancelled():
            return self._stop or bool(cancelled and cancelled())

        def design(message, decision=None):
            return run(message, llm=self.llm, tools=tools, events=stream,
                       cancelled=is_cancelled,
                       aborted=lambda: self._abort,
                       attachments=list(attachments), gate=gate, ask=answered_by,
                       decide=decide,
                       outstanding=list(outstanding),
                       the_part_path=the_part_path)

        def run_question(message, decision):
            return "".join(chunk.text for chunk in self.session.turn(self.llm, message)
                           if chunk.kind == TEXT)

        def control(message, decision):
            if "abort" in message.lower():
                self._abort = True
            self._stop = True

        if route_first:
            return route(prompt, llm=self.llm, events=stream,
                         handlers={DESIGN: design, RUN_QUESTION: run_question, CONTROL: control},
                         session=self.session)
        else:
            return design(prompt)

    def resume(self, answer):
        """
        Answer the question a paused run is waiting on, and pick that same
        run back up -- *answer* reaches only the question that paused it,
        then the run carries on as `send` normally would.
        """
        pending, self._pending = self._pending, None
        if pending is None:
            raise RuntimeError("nothing is paused")
        return self.send(pending["prompt"], gate=pending["gate"], decide=pending["decide"],
                         ask=lambda question: answer, attachments=pending["attachments"])

    def save(self, path):
        """Write this Client's Document to *path* and hand the path back."""
        self.document.saveAs(path)
        return path

    def close(self):
        """Let the kernel go and close the Document. A Client is not reused after this."""
        if self.kernel is not None:
            self.kernel.kill()
        import FreeCAD

        FreeCAD.closeDocument(self.document.Name)
