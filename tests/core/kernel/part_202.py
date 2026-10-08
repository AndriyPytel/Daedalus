"""The part 202 checkpoint a CADGenBench edit run leaves behind, opened for a test to time something against."""
import os

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "part_202.FCStd")


def opened():
    """
    The fixture as a recomputed Document: a Part::Feature source, a Body whose BaseFeature it is, and one Pad.
    Only the source's shape is stored; the Body and the Pad are rebuilt here.
    """
    import FreeCAD

    doc = FreeCAD.openDocument(FIXTURE)
    for obj in doc.Objects:
        obj.touch()
    doc.recompute()
    return doc


def saved_whole(directory):
    """A recomputed copy of the fixture on disk, for a kernel to be handed."""
    doc = opened()
    path = os.path.join(directory, "part_202.FCStd")
    doc.saveAs(path)
    return path
