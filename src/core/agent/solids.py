"""
The solids a Document holds: the objects nothing else is built on that carry a shape with at least one solid.
"""


def top_solids(doc):
    """The objects of *doc* that no other object uses and whose shape holds a solid."""
    return [o for o in doc.Objects
            if not o.InList and getattr(o, "Shape", None) is not None
            and not o.Shape.isNull() and o.Shape.Solids]
