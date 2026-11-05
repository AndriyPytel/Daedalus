"""
A trimmed copy of the FreeCAD-library's git tree, a FreeCAD file as bytes, and a `get` that answers from them without a network.
"""
import io
import json
import zipfile

from core.furniture import RAW

TREE = {"tree": [{"path": path, "type": "blob"} for path in (
    "Architectural Parts/Bedroom/FCBL_bed_double.FCStd",
    "Architectural Parts/Bedroom/Images/FCBL_bed_double.png",
    "Architectural Parts/Bedroom/Beds.FCStd",
    "Architectural Parts/Hydro equipment/WallHungToilet.FCStd",
    "Architectural Parts/Hydro equipment/WallHungToilet.step",
    "Architectural Parts/Chairs/Office_chair.FCStd",
    "Mechanical Parts/Gears/Spur gear.FCStd",
)]}
PROPERTY = '<Property name="%s" type="App::PropertyString"><String value="%s"/></Property>'


def library_file(created_by="Ada Lovelace", licence="", licence_url=""):
    """The bytes of a FreeCAD file whose Document.xml carries the given properties."""
    properties = "".join(PROPERTY % pair for pair in (("CreatedBy", created_by), ("License", licence),
                                                      ("LicenseURL", licence_url)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Document.xml", "<Document><Properties>%s</Properties></Document>" % properties)
    return buffer.getvalue()


class FakeGet:
    """Records every URL asked for; answers the tree for the index, *history* (newest commit first) for the commits and *body* for anything else."""

    def __init__(self, body=None, history=()):
        self.urls = []
        self.body = library_file() if body is None else body
        self.history = [{"commit": {"author": {"name": name}}} for name in history]

    def __call__(self, url):
        self.urls.append(url)
        if "/git/trees/" in url:
            return json.dumps(TREE).encode()
        if "/commits?" in url:
            return json.dumps(self.history).encode()
        return self.body
