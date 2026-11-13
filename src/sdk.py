"""
The one assembly the addon panel and an external caller both use. `Client`
owns a Session, a Kernel and a Document, and turns a prompt into whichever of
a design request, a run question, or a control instruction the routing orchestrator
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

from core import events, model_catalog
from core import settings as _settings
from core.arch import arch_type, export_ifc, named_in
from core import furniture, nativeifc
from core.equipment import write_attribution
from core.freecad_apply import ApplyPipeline, active_document, build_registry
from core.events import (APPLIED, ASSERTION, BLOCKED, COMPLETED, COST, REVIEW_VERDICT,
                         EventStream, replay)
from core.kernel import Kernel
from core.llm import (EFFORTS, call_ceiling, TEXT, ScriptedLLMClient, Session, litellm_model_name,
                      unusable_attachments)
from core.llm.factory import client_for, client_from_settings, effort
from core.model_catalog import configured
from core.agent.assembly import PART_SHARE, part_summary, run_assembly, unfinished
from core.agent.bim import run_bim
from core.agent.domain import ASSEMBLY, BIM, PART, label as domain_label
from core.agent.infer import AUTO, CHOICES, choose
from core.agent.reader import run_reader
from core.narrate import narrate
from core.orchestrator import CONTROL, DESIGN, RUN_QUESTION, route
from core.prompts import read_prompt
from core.session_title import fallback, generate
from core.sessions import Sessions, log_id

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
    """Every provider name the Providers page and the model picker can offer."""
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


def get_domain():
    return _settings.instance().get_domain()


def set_domain(choice):
    _settings.instance().set_domain(choice)


def get_last_model():
    """The ``(provider, model)`` last picked in the chat, or ``("", "")``."""
    return _settings.instance().get_last_model()


def set_last_model(provider, model):
    _settings.instance().set_last_model(provider, model)


def get_effort():
    """The Effort in force: the one stored, or the default where none is stored or the stored one is not offered."""
    return effort()


def set_effort(level):
    _settings.instance().set_effort(level)


def fetch_models(provider, api_key, base_url=None):
    """``[{id, display}]`` *provider* actually reports for *api_key*."""
    return model_catalog.fetch(provider, api_key, base_url=base_url)


class _NoKernel:
    """Sentinel for Client(kernel=NO_KERNEL) to run in-process without a subprocess."""
    pass


NO_KERNEL = _NoKernel()


def native_document(ifc, work, path):
    """Save to *path* a Document holding *ifc* as a nativeifc project, edited in place at *work*, and hand the path back."""
    import FreeCAD

    document = FreeCAD.newDocument("Native")
    nativeifc.open_ifc(document, ifc, work)
    document.saveAs(path)
    FreeCAD.closeDocument(document.Name)
    return path


class Client:
    """
    *document* is a path to open, `None` for a fresh one, or an already-open Document object; *llm* and *kernel* default to the chat's model and a real headless kernel, and `NO_KERNEL` runs in-process without one.
    *confirm* is asked before an object the assistant did not create is deleted, and left out nothing is asked and the deletion is refused; *dispatch* carries every touch of the Document, its open included, because `FreeCAD.newDocument` off the main thread aborts FreeCAD on macOS.
    *history* is the conversation to continue and *on_document* is told the Document's name at each send.
    """

    def __init__(self, document=None, llm=None, kernel=None,
                 confirm=None, dispatch=None, history=(), on_document=None):
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

        self._injected = self._live = llm
        self._intent = None
        self._on_document = on_document
        self._confirm = confirm or (lambda kinds: False)
        self.events = EventStream()
        self.session = Session()
        self.session.messages.extend(history)
        self._pipeline = ApplyPipeline(
            document=self.document,
            worker=self.kernel,
            dispatch=self.dispatch,
            confirm=self._asked,
            events=self.events,
            unattended=confirm is None)
        self._stop = False

    def _asked(self, kinds):
        """Consent to *kinds*: the confirm callable's yes, or, in an edit of a flat, a deletion of what the intent names."""
        if self._confirm(kinds) is True:
            return True
        names = kinds.get("delete")
        return bool(self._intent and names
                    and self.dispatch(lambda: named_in(self._intent, self.document, names)))

    def _the_llm(self):
        """The client a run is made with: the one handed in, else the chat's model and Effort as they are now."""
        self._live = self._injected or client_from_settings()
        return self._live

    def watch(self, callback):
        """Subscribe *callback* to every event a `send` emits, as it happens."""
        self.events.subscribe(callback)

    def send(self, prompt, attachments=(), cancelled=None, route_first=True, events=None,
             choice=AUTO):
        """
        Run *prompt* to the end, blocking: the GUI is locked where there is one, *route_first* classifies the prompt via the orchestrator, and a design builds under the Domain *choice* names or, left `AUTO`, the one inferred.
        *events* overrides this Client's stream for this run only.
        """
        stream = events if events is not None else self.events
        self._pipeline.events = stream

        self._stop = False
        llm = self._the_llm()
        tools = build_registry(document=self.document, pipeline=self._pipeline,
                               dispatch=self.dispatch)

        def is_cancelled():
            return self._stop or bool(cancelled and cancelled())

        def design(message, decision=None):
            types, arch = self.dispatch(lambda: ([o.TypeId for o in self.document.Objects],
                                                 any(arch_type(o) for o in self.document.Objects)))
            domain = choose(choice, message, types, llm, stream, arch=arch)
            return self.agent(message, attachments=attachments, cancelled=is_cancelled,
                              base=tools.call("base_inventory")["inventory"] is not None,
                              domain=domain, events=stream, llm=llm)

        def run_question(message, decision):
            return "".join(chunk.text for chunk in self.session.turn(llm, message)
                           if chunk.kind == TEXT)

        def control(message, decision):
            self._stop = True

        import FreeCAD

        locked = bool(FreeCAD.GuiUp)
        if locked:
            self._pipeline.lock()
        try:
            if self._on_document:
                self._on_document(str(tools.call("snapshot").get("document") or ""))
            if route_first:
                return route(prompt, llm=llm, events=stream,
                             handlers={DESIGN: design, RUN_QUESTION: run_question, CONTROL: control},
                             session=self.session)
            return design(prompt)
        finally:
            if locked:
                self._pipeline.end_run()

    def part_builder(self, folder, cancelled, budget_usd, events, llm):
        """
        The `build_part` of the Assembly Domain: each call runs the Part Domain in a Client of its own, with its own kernel, Document and Session, and saves its Body in *folder*.
        Only its cost reaches *events*, and the call ends once it has spent `PART_SHARE` of *budget_usd*.
        """
        def build(name, intent, attachments):
            spent = {"usd": 0.0}
            share = PART_SHARE * budget_usd if budget_usd is not None else None

            def over():
                return share is not None and spent["usd"] >= share

            def forward(event):
                if event.kind == COST:
                    spent["usd"] += event.data.get("usd") or 0.0
                    events.emit(COST, **event.data)

            sub = Client(llm=llm, dispatch=self.dispatch)
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
              budget_usd=None, events=None, llm=None):
        """
        The loop under *domain*: for the Part Domain the drawing is read into a table, then one model
        builds over this Client's kernel and lands each code call on this
        Client's Document, so `save` and `export_step` write what it built.
        *base* says the Document already holds the part to edit; under the Assembly Domain it is the Assembly the intent changes, and *budget_usd* is the cap its parts' shares are taken from.
        *events* overrides this Client's stream for this run only, and *llm* its model.
        """
        if self.kernel is None:
            raise ValueError("the agent works through a kernel")
        if domain not in (PART, ASSEMBLY, BIM):
            raise ValueError("no such Domain: %r" % domain)
        self._intent = prompt if base and domain != PART else None
        llm = llm or self._the_llm()
        stream = events if events is not None else self.events
        self._pipeline.events = stream
        tools = build_registry(document=lambda: self.document, pipeline=self._pipeline,
                               dispatch=self.dispatch)
        self._pipeline.start_run()
        if domain == ASSEMBLY:
            with tempfile.TemporaryDirectory(prefix="fcc-parts-") as folder:
                return run_assembly(prompt, llm=llm, kernel=self.kernel, tools=tools,
                                    events=stream, attachments=list(attachments),
                                    cancelled=cancelled,
                                    build_part=self.part_builder(folder, cancelled, budget_usd, stream, llm),
                                    edit=base)
        if domain == BIM:
            native = bool(base and self.dispatch(lambda: nativeifc.projects(self.document)))
            if native:
                nativeifc.without_geometry()
            return run_bim(prompt, llm=llm, kernel=self.kernel, tools=tools, events=stream,
                           attachments=list(attachments), cancelled=cancelled, edit=base, native=native)
        return run_reader(prompt, llm=llm, kernel=self.kernel, tools=tools,
                          events=stream, attachments=list(attachments),
                          cancelled=cancelled, base=base)

    def stop(self):
        """End the run in flight at once: it is cancelled between calls and the provider call under way is killed, the kernel left to checkpoint its Step."""
        from core.llm.claude import kill

        self._stop = True
        kill(self._live)

    def shutdown(self):
        """Kill the kernel now, so the Step in flight comes back a crash and stays out of the Document."""
        self._pipeline.shutdown()

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

    def export_ifc(self, path):
        """Write this Client's Arch objects to *path* as IFC4 and hand the path back, or `None` where it holds none; where library furniture is in it, `<stem>.ATTRIBUTION.md` goes beside it."""
        written = self.dispatch(lambda: nativeifc.export(self.document, path)) or export_ifc(self.document, path)
        if written:
            write_attribution(self.document, written, furniture.index(furniture.CACHE, furniture.http_get))
        return written

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
