"""Reading an EventStream the way tests do: by kind, in order."""


def events_of(stream, kind):
    return [event for event in stream if event.kind == kind]


def first_event(stream, kind):
    return next((event for event in stream if event.kind == kind), None)


def log_of(stream):
    return "\n".join(event.as_json() for event in stream)
