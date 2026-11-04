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
import importlib
import os
import re
import tempfile

from core import events, freecad_apply, model_catalog
from core import settings as _settings
from core.freecad_apply import ApplyPipeline, active_document, build_registry
from core.events import (ASSERTION, BLOCKED, COST, REVIEW_VERDICT, EventStream,
                         replay)
from core.kernel import Kernel
from core.llm import (EFFORTS, ORCHESTRATOR, TEXT, ScriptedLLMClient, Session,
                      client_from_roles, effort_is_honoured, selected_model_name,
                      unusable_attachments)
from core.llm.factory import client_from_settings
from core.loop import (ACCEPT, APPROVE, ASSERTION_PHASE, DECISIONS, DESIGN_PHASE,
                       EXECUTE_PHASE, PLAN_PHASE, REJECT, REVIEW_PHASE, REVISE,
                       START_OVER, Approval, NeedsAnswer, auto_approve,
                       nobody_to_ask, revise_by_default, run)
from core.loop.budgets import Budgets
from core.agent.assembly import PART_SHARE, part_summary, run_assembly, unfinished
from core.agent.domain import ASSEMBLY, PART
from core.agent.reader import run_reader
from core.narrate import narrate
from core.orchestrator import CONTROL, DESIGN, RUN_QUESTION, route
from core.prompts import read_prompt
from core.session_title import fallback, generate
from core.sessions import Sessions

_LAZY = {
    "manifold": ("core.manifold", None),
    "repair_base": ("core.manifold", "repair_base"),
    "rewrite_step": ("core.manifold", "rewrite_step"),
    "average": ("core.measure", "_average"),
    "pass_rate": ("core.measure", "_pass_rate"),
    "rate": ("core.measure", "_rate"),
    "compare": ("core.measure", "compare"),
    "measure": ("core.measure", "measure"),
    "score_spread": ("core.measure", "score_spread"),
    "betti_numbers": ("core.topology", "betti_numbers"),
    "topology_score": ("core.topology", "topology_score"),
}


def __getattr__(name):
    """The names `_LAZY` lists, imported on first use: `manifold` needs OCC and the scorer's venv has none, so `import sdk` must not load it."""
    if name not in _LAZY:
        raise AttributeError(name)
    module, attribute = _LAZY[name]
    loaded = importlib.import_module(module)
    return getattr(loaded, attribute) if attribute else loaded


def providers():
    """Every provider name a role or the Providers page can offer."""
    return _settings.instance().get_providers()


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


def set_api_key_for_provider(provider, key):
    """Upsert the stored key for *provider*."""
    _settings.instance().set_api_key_for_provider(provider, key)


def get_base_urls():
    """``{provider: base_url}`` for every provider that has an address."""
    return _settings.instance().get_base_urls()


def set_base_urls(urls):
    """Replace the stored address map."""
    _settings.instance().set_base_urls(urls)


def get_base_url_for_provider(provider):
    """The stored server address for *provider*, or ``''`` if unset."""
    return _settings.instance().get_base_url_for_provider(provider)


def set_base_url_for_provider(provider, url):
    """Upsert the stored address for *provider*, or drop it when *url* is empty."""
    _settings.instance().set_base_url_for_provider(provider, url)


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


def remember_model(model_id, provider):
    """Name *provider* as the one that reports *model_id*, as the panel's fetch does, for a process that runs no panel."""
    from core import model_registry

    model_registry.remember(model_id, provider, model_id)


def remember_listed(provider):
    """Ask *provider* for its model list with the stored key and address and remember every model on it, as the panel's fetch does."""
    from core import model_registry

    sm = _settings.instance()
    for model in model_catalog.fetch(provider, sm.get_api_key_for_provider(provider),
                                     base_url=sm.get_base_url_for_provider(provider) or None):
        model_registry.remember(model["id"], provider, model["display"], model.get("max_output"))


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
            self.document.UndoMode = 1
        elif isinstance(document, str):
            self.document = self.dispatch(lambda: FreeCAD.openDocument(document))
            self.document.UndoMode = 1
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
             route_first=True, events=None, budgets=None, arm="agent", domain=PART):
        """
        Run *prompt* to the end, blocking. *route_first* classifies it via the orchestrator (default), and *events* overrides this Client's stream for this run only.
        *arm* `agent` builds with the loop under *domain*; `pipeline` runs the phased one, which alone reads *gate*, *ask*, *decide*, *outstanding*, *the_part_path* and *budgets*, a harness affordance the panel never passes.
        """
        stream = events if events is not None else self.events
        self.pipeline.events = stream

        self._stop = False
        self._abort = False
        tools = build_registry(document=self.document, pipeline=self.pipeline,
                               dispatch=self.dispatch)

        def _pause(question):
            self._pending = dict(prompt=prompt, gate=gate, decide=decide,
                                 attachments=attachments, arm=arm, domain=domain)
            raise NeedsAnswer(question)

        answered_by = ask or self.answer or _pause

        def is_cancelled():
            return self._stop or bool(cancelled and cancelled())

        def design(message, decision=None):
            if arm == "agent":
                return self.agent(message, attachments=attachments, cancelled=is_cancelled,
                                  base=tools.call("base_inventory")["inventory"] is not None,
                                  domain=domain)
            return run(message, llm=self.llm, tools=tools, events=stream,
                       cancelled=is_cancelled,
                       aborted=lambda: self._abort,
                       budget_exceeded=lambda: bool(cancelled and cancelled()),
                       attachments=list(attachments), gate=gate, ask=answered_by,
                       decide=decide,
                       outstanding=list(outstanding),
                       the_part_path=the_part_path, kernel=self.kernel, budgets=budgets)

        orchestrator = (self.llm.for_role(ORCHESTRATOR)
                        if hasattr(self.llm, "for_role") else self.llm)

        def run_question(message, decision):
            return "".join(chunk.text for chunk in self.session.turn(orchestrator, message)
                           if chunk.kind == TEXT)

        def control(message, decision):
            if "abort" in message.lower():
                self._abort = True
            self._stop = True

        if route_first:
            return route(prompt, llm=orchestrator, events=stream,
                         handlers={DESIGN: design, RUN_QUESTION: run_question, CONTROL: control},
                         session=self.session)
        else:
            return design(prompt)

    def part_builder(self, folder, cancelled, budget_usd):
        """
        The `build_part` of the Assembly Domain: each call runs the Part Domain in a Client of its own, with its own kernel, Document and Session, and saves its Body in *folder*.
        Only its cost reaches this Client's events, and the call ends once it has spent `PART_SHARE` of *budget_usd*.
        """
        def build(name, intent, attachments):
            spent = {"usd": 0.0}
            share = PART_SHARE * budget_usd if budget_usd is not None else None

            def over():
                return share is not None and spent["usd"] >= share

            def forward(event):
                if event.kind == COST:
                    spent["usd"] += event.data.get("usd") or 0.0
                    self.events.emit(COST, **event.data)

            sub = Client(llm=self.llm, dispatch=self.dispatch)
            sub.events.subscribe(forward)
            try:
                result = sub.agent(intent, attachments=attachments, domain=PART,
                                   cancelled=lambda: over() or bool(cancelled and cancelled()))
                measured = part_summary(sub.document)
                path = None
                if measured is not None:
                    path = sub.save(os.path.join(folder, re.sub(r"\W", "_", name) + ".FCStd"))
            finally:
                sub.close()
            reason = "the part's share of the budget was spent" if over() else result.reason
            return {"name": name, "ok": bool(result.ok and measured and not over()), "reason": reason,
                    "path": path, "usd": round(spent["usd"], 4), **(measured or {})}

        return build

    def agent(self, prompt, attachments=(), cancelled=None, base=False, domain=PART,
              budget_usd=None):
        """
        The loop under *domain*: for the Part Domain the drawing is read into a table, then one model
        builds over this Client's kernel and lands each code call on this
        Client's Document, so `save` and `export_step` write what it built.
        *base* says the Document already holds the part to edit; under the Assembly Domain it is the Assembly the intent changes, and *budget_usd* is the cap its parts' shares are taken from.
        """
        if self.kernel is None:
            raise ValueError("the agent works through a kernel")
        if domain not in (PART, ASSEMBLY):
            raise ValueError("no such Domain: %r" % domain)
        self.pipeline.events = self.events
        tools = build_registry(document=lambda: self.document, pipeline=self.pipeline,
                               dispatch=self.dispatch)
        seed = tools.call("document_copy")["path"]
        self.kernel.check("pass", document=seed)
        freecad_apply.discard(seed)
        if domain == ASSEMBLY:
            with tempfile.TemporaryDirectory(prefix="fcc-parts-") as folder:
                return run_assembly(prompt, llm=self.llm, kernel=self.kernel, tools=tools,
                                    events=self.events, attachments=list(attachments),
                                    cancelled=cancelled,
                                    build_part=self.part_builder(folder, cancelled, budget_usd),
                                    edit=base)
        return run_reader(prompt, llm=self.llm, kernel=self.kernel, tools=tools,
                          events=self.events, attachments=list(attachments),
                          cancelled=cancelled, base=base)

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
                         ask=lambda question: answer, attachments=pending["attachments"],
                         arm=pending["arm"], domain=pending["domain"])

    def kill_provider(self):
        """End this Client's own live provider process at once."""
        from core.llm.claude import kill

        kill(self._llm)

    def save(self, path):
        """Write this Client's Document to *path* and hand the path back."""
        self.document.saveAs(path)
        return path

    def export_step(self, path, only=None):
        """
        Write this Client's modelled geometry to *path* as STEP, and hand
        the path back -- `save` writes a Document; `saveAs` would otherwise
        append `.FCStd` to a STEP-shaped name and leave no STEP behind. *only* names the one object written.
        """
        from core.freecad_apply import export_step

        return export_step(self.document, path, only)

    def has_solid(self):
        """Whether this Client's Document holds a solid; a document without one has no STEP to write."""
        from core.freecad_apply import has_solid

        return has_solid(self.document)

    def close(self):
        """Let the kernel go and close the Document. A Client is not reused after this."""
        if self.kernel is not None:
            self.kernel.kill()
        import FreeCAD

        FreeCAD.closeDocument(self.document.Name)
