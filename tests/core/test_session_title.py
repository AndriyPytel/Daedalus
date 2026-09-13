"""
Level 1 — the short call that names a Session from its first Intent.

The fallback is the interesting half: a Session is never called "Chat 3" and
never sits under an empty name, so something readable has to exist before the
call returns and has to survive the call failing.
"""
from core.llm import ProviderRefused, ScriptedLLMClient
from core.session_title import LIMIT, fallback, generate


def test_a_short_intent_is_its_own_fallback_name():
    assert fallback("A bracket with two bores") == "A bracket with two bores"


def test_a_long_intent_is_truncated_rather_than_emptied():
    intent = "Model a spur gear with 24 teeth, module 2, 10mm thick, and a 12mm bore"

    name = fallback(intent)

    assert len(name) <= LIMIT + 1
    assert name.startswith("Model a spur gear")
    assert not name.endswith(" …")


def test_the_fallback_flattens_a_multi_line_intent():
    assert fallback("a bracket\n\n  with two bores") == "a bracket with two bores"


def test_an_intent_of_only_whitespace_still_names_the_session():
    assert fallback("   \n ") == "Untitled Session"


def test_the_generated_title_is_what_the_model_said():
    client = ScriptedLLMClient(["Two-bore mounting bracket"])

    assert generate("make me a bracket with two bores", client=client) == \
        "Two-bore mounting bracket"


def test_the_intent_is_what_the_model_is_asked_about():
    client = ScriptedLLMClient(["Spur gear"])

    generate("a spur gear, module 2", client=client)

    assert client.calls[0][-1]["content"] == "a spur gear, module 2"


def test_a_title_is_stripped_of_the_decoration_models_add():
    client = ScriptedLLMClient(['  "Two-bore bracket."\n\nLet me know if…  '])

    assert generate("x", client=client) == "Two-bore bracket"


def test_a_refusal_produces_no_title_rather_than_an_exception():
    """A name is never worth failing a run over — the fallback stays."""
    client = ScriptedLLMClient([ProviderRefused("out of quota")])

    assert generate("x", client=client) == ""


def test_a_model_that_answers_with_nothing_produces_no_title():
    assert generate("x", client=ScriptedLLMClient([""])) == ""


def test_a_runaway_answer_is_not_accepted_as_a_title():
    """A model that ignores the instruction gives back a paragraph, not a name."""
    client = ScriptedLLMClient(["A bracket " * 40])

    assert generate("x", client=client) == ""


def test_a_model_that_cannot_be_resolved_is_no_title_rather_than_a_crash(monkeypatch):
    """
    `client_from_settings` raises ValueError when the model id is in no
    provider's list — which happens whenever a key change clears the fetched
    lists. Built outside the try, that escaped `_TitleThread.run` unhandled and
    the Session was then silently never named.
    """
    import core.llm
    from core.session_title import generate

    def refuse(model_id=None):
        raise ValueError("Unknown model 'gpt-5.6-terra'. Check your Preferences.")

    monkeypatch.setattr(core.llm, "client_from_settings", refuse)

    assert generate("Make a bracket") == ""
