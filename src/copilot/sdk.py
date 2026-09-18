"""
The one assembly the addon panel and an external caller both use. `Client`
owns a Session, a Kernel and a Document, and turns a prompt into whichever of
Design, a run question, or a control instruction the routing orchestrator
names; a script drives it with no Qt application and no `FreeCADGui`, because
`ApplyPipeline`'s own `dispatch` and `confirm` are plain callables here
rather than the main-thread marshaller a GUI needs. `run.py` and `lane.py`,
the two surviving scripts in the harness directory, drive this same object to
start a run.
"""
from core import events, model_catalog
from core import settings as _settings
from core.freecad_apply import ApplyPipeline, active_document, build_registry
from core.events import EventStream, replay
from core.kernel import Kernel
from core.llm import (EFFORTS, ORCHESTRATOR, TEXT, Session, client_from_roles,
                      effort_is_honoured, selected_model_name,
                      unusable_attachments)
from core.loop import (ACCEPT, APPROVE, ASSERTION_PHASE, DECISIONS, DESIGN_PHASE,
                       EXECUTE_PHASE, PLAN_PHASE, REJECT, REVIEW_PHASE, REVISE,
                       START_OVER, Approval, NeedsAnswer, auto_approve,
                       revise_by_default, run)
from core.narrate import narrate
from core.orchestrator import CONTROL, DESIGN, RUN_QUESTION, route
from core.session_title import fallback, generate
from core.sessions import Sessions


def providers():
    """Every provider name a role or the Providers page can offer."""
    return model_catalog.providers()


def needs_base_url(provider):
    """Whether *provider* takes a base URL (a local server, not a hosted one)."""
    return model_catalog.needs_base_url(provider)


def needs_no_key(provider):
    """Whether *provider* signs itself in, so the Providers page stores no key for it."""
    return model_catalog.needs_no_key(provider)


def get_api_keys():
    """Every stored ``{provider, api_key}``."""
    return _settings.instance().get_api_keys()


def set_api_keys(keys):
    """Replace the stored key list."""
    _settings.instance().set_api_keys(keys)


def get_api_key_for_provider(provider):
    """The stored key for *provider*, or ``''`` if there is none."""
    return _settings.instance().get_api_key_for_provider(provider)


def get_base_urls():
    """``{provider: base_url}`` for every provider that has an address."""
    return _settings.instance().get_base_urls()


def set_base_urls(urls):
    """Replace the stored address map."""
    _settings.instance().set_base_urls(urls)


def get_base_url_for_provider(provider):
    """The stored server address for *provider*, or ``''`` if unset."""
    return _settings.instance().get_base_url_for_provider(provider)


def get_default_model():
    return _settings.instance().get_default_model()


def set_default_model(model_id):
    _settings.instance().set_default_model(model_id)


def get_naming_model():
    return _settings.instance().get_naming_model()


def set_naming_model(model_id):
    _settings.instance().set_naming_model(model_id)


def get_last_model():
    return _settings.instance().get_last_model()


def set_last_model(model_id):
    _settings.instance().set_last_model(model_id)


def get_auto_approve_plan():
    return _settings.instance().get_auto_approve_plan()


def set_auto_approve_plan(enabled):
    _settings.instance().set_auto_approve_plan(enabled)


def get_effort():
    return _settings.instance().get_effort()


def set_effort(level):
    _settings.instance().set_effort(level)


def get_role_table():
    """One row per role -- ``provider``, ``model``, ``effort``."""
    return _settings.instance().get_role_table()


def set_role_table(table):
    _settings.instance().set_role_table(table)


def get_role_model(role):
    """The stored row for *role*, or ``None`` if it has none."""
    return _settings.instance().get_role_model(role)


def get_llm_credentials(model_id=None):
    """``(provider, model_id, api_key)`` for *model_id*, or the last-used one."""
    return _settings.instance().get_llm_credentials(model_id)


def fetch_models(provider, api_key, base_url=None):
    """``[{id, display}]`` *provider* actually reports for *api_key*."""
    return model_catalog.fetch(provider, api_key, base_url=base_url)


def get_model_entry(model_id):
    """The last-known ``{id, display, provider}`` for *model_id*, or ``None``."""
    from core import model_registry

    return model_registry.get_model_entry(model_id)


class _NoKernel:
    """Sentinel for Client(kernel=NO_KERNEL) to run in-process without a subprocess."""
    pass


NO_KERNEL = _NoKernel()


class Client:
    """
    *document* is a path to open, `None` for a fresh one, or an already-open
    FreeCAD Document object (live-document mode, so the panel binds to the
    Document it already has). *llm* and *kernel* default to a real provider
    and a real headless kernel, a test hands in its own, and `NO_KERNEL` runs
    in-process without a subprocess. Without an *answer* callback `send`
    pauses in a blocked, resumable `needs-answer` state rather than guessing,
    and *confirm* and *dispatch* set the corresponding `ApplyPipeline` callables,
    *dispatch* also carrying the open or create of the Document itself, since
    `FreeCAD.newDocument` on a worker thread has the GUI instantiate an MDI
    window off the main thread and macOS aborts FreeCAD for it.
    """

    def __init__(self, document=None, llm=None, kernel=None, answer=None,
                 confirm=None, dispatch=None):
        import FreeCAD

        self.dispatch = dispatch or (lambda func, timeout=None: func())

        if document is None:
            self.document = self.dispatch(FreeCAD.newDocument)
        elif isinstance(document, str):
            self.document = self.dispatch(lambda: FreeCAD.openDocument(document))
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
            dispatch=self.dispatch,
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
             route_first=True, events=None, budgets=None):
        """
        Run *prompt* through the loop, blocking until done. *route_first*
        classifies via the orchestrator first (default); `False` skips straight
        to design. *events* overrides this Client's stream for this run only,
        *budgets* (`core.loop.budgets.Budgets`) is a harness affordance for
        measuring the loop itself that the panel never passes.
        """
        stream = events if events is not None else self.events
        self.pipeline.events = stream

        self._stop = False
        self._abort = False
        tools = build_registry(document=self.document, pipeline=self.pipeline,
                               dispatch=self.dispatch)

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
                       budget_exceeded=lambda: bool(cancelled and cancelled()),
                       attachments=list(attachments), gate=gate, ask=answered_by,
                       decide=decide,
                       outstanding=list(outstanding),
                       the_part_path=the_part_path, kernel=self.kernel, budgets=budgets)

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

    def kill_provider(self):
        """End this Client's own live provider process at once."""
        from core.llm.claude import kill

        kill(self._llm)

    def save(self, path):
        """Write this Client's Document to *path* and hand the path back."""
        self.document.saveAs(path)
        return path

    def export_step(self, path):
        """
        Write this Client's modelled geometry to *path* as STEP, and hand
        the path back -- `save` writes a Document; `saveAs` would otherwise
        append `.FCStd` to a STEP-shaped name and leave no STEP behind.
        """
        from core.freecad_apply import export_step

        return export_step(self.document, path)

    def close(self):
        """Let the kernel go and close the Document. A Client is not reused after this."""
        if self.kernel is not None:
            self.kernel.kill()
        import FreeCAD

        FreeCAD.closeDocument(self.document.Name)
