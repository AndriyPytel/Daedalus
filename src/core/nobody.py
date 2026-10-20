"""
Whether a person answers the run. `core.loop.run` sets it for a headless run, whose `ask` is
`nobody_to_ask`, and the prompts and the merge read it to behave as a run nobody watches does.
Worker threads need `contextvars.copy_context`.
"""
import contextlib
import contextvars

_nobody_answers = contextvars.ContextVar("nobody_answers", default=False)


@contextlib.contextmanager
def nobody_answering():
    token = _nobody_answers.set(True)
    try:
        yield
    finally:
        _nobody_answers.reset(token)


def nobody_is_answering() -> bool:
    return _nobody_answers.get()
