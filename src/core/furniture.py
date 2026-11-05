"""
Furniture for the BIM Domain comes from the FreeCAD-library (CC-BY 3.0) alone, found in its git tree, cached on disk, and fetched only from the URL prefixes in `WHITELIST`.
Where nothing fits, a parametric box of the kind's typical size stands in; a model the Part Domain generates is placed only on explicit request.
The calls answer with the code that places an item in the Document, which the loop runs as a `code` call.
"""
import json
import os
import zipfile
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

WHITELIST = ("https://raw.githubusercontent.com/FreeCAD/FreeCAD-library/",
             "https://api.github.com/repos/FreeCAD/FreeCAD-library/",
             "https://github.com/FreeCAD/FreeCAD-library/")
RAW = WHITELIST[0] + "master/"
INDEX_URL = WHITELIST[1] + "git/trees/master?recursive=1"
LICENCE = "CC-BY 3.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/3.0/"
HISTORY_URL = "https://github.com/FreeCAD/FreeCAD-library/commits/master/"
UNSET_LICENCE = "All rights reserved"
UNRECORDED = "author not recorded"
PARAMETRIC = "parametric"
GENERATED = "generated on request"
CACHE = os.path.expanduser("~/.cache/daedalus/furniture")
PARTS = "Architectural Parts/"
SIZES = {"bed": (1600, 2000, 500), "sofa": (2000, 900, 850), "table": (1600, 900, 750),
         "chair": (450, 450, 900), "wardrobe": (1000, 600, 2100), "toilet": (400, 700, 400),
         "basin": (600, 450, 850), "bath": (1700, 750, 550), "shower": (900, 900, 2000),
         "fridge": (600, 650, 1800), "cooker": (600, 600, 900), "sink": (1200, 600, 900)}
FOUND = 8


class Refused(ValueError):
    """A URL outside the whitelist was asked for."""


def http_get(url):
    import httpx

    try:
        response = httpx.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OSError(str(exc)) from exc
    return response.content


def permitted(url):
    """Whether *url* is under a whitelisted prefix and climbs out of none of them."""
    return url.startswith(WHITELIST) and ".." not in urlparse(url).path.split("/")


def short(source):
    """*source* without the library's raw URL prefix, which is what the Assertion's facts carry."""
    return source[len(RAW):] if source.startswith(RAW) else source


def sourced(source, entries):
    """Whether *source*, shortened, is `parametric`, `generated on request` or a file of the library's *entries*."""
    return source in (PARAMETRIC, GENERATED) or source in entries


def fetch(url, get):
    """The bytes *get* returns for *url*, or `Refused` where *url* is outside the whitelist and nothing is asked for."""
    if not permitted(url):
        raise Refused("%s is outside the whitelist of the FreeCAD-library" % url)
    return get(url)


def index(cache, get):
    """The library's FreeCAD files under Architectural Parts, read from the git tree once and from *cache* after."""
    path = os.path.join(cache, "index.json")
    if not os.path.exists(path):
        tree = json.loads(fetch(INDEX_URL, get))["tree"]
        found = sorted(e["path"] for e in tree
                       if e["type"] == "blob" and e["path"].startswith(PARTS) and e["path"].endswith(".FCStd"))
        os.makedirs(cache, exist_ok=True)
        with open(path, "w") as out:
            json.dump(found, out)
    with open(path) as source:
        return json.load(source)


def title(path):
    """The words of a library file's name."""
    return os.path.splitext(os.path.basename(path))[0].replace("_", " ")


def find(query, entries):
    """Up to eight candidates whose path holds the most words of *query*, best first; none where no word matches."""
    words = query.lower().split()
    scored = sorted(((sum(w in e.lower() for w in words), e) for e in entries), key=lambda s: -s[0])
    return [{"title": title(e), "path": e, "url": RAW + e, "licence": LICENCE}
            for score, e in scored[:FOUND] if score]


def download(candidate, cache, get):
    """The local path of the candidate's file, fetched once into *cache*."""
    path = os.path.join(cache, candidate["path"].replace("/", "__"))
    if not os.path.exists(path):
        data = fetch(RAW + candidate["path"], get)
        os.makedirs(cache, exist_ok=True)
        with open(path, "wb") as out:
            out.write(data)
    return path


def properties(local):
    """The `CreatedBy`, `License` and `LicenseURL` the FreeCAD file at *local* carries, empty where unset; FreeCAD's own default licence counts as unset."""
    with zipfile.ZipFile(local) as archive:
        root = ElementTree.fromstring(archive.read("Document.xml"))
    found = {}
    for node in root.iter("Property"):
        value = node.find("String")
        if node.get("name") in ("CreatedBy", "License", "LicenseURL") and value is not None:
            found[node.get("name")] = (value.get("value") or "").strip()
    if found.get("License") == UNSET_LICENCE:
        found["License"] = found["LicenseURL"] = ""
    return {name: found.get(name, "") for name in ("CreatedBy", "License", "LicenseURL")}


def first_author(path, cache, get):
    """Who first committed *path* to the library, read from the commits API once into *cache*, or `None` where the history gives none."""
    known = os.path.join(cache, "authors.json")
    authors = json.load(open(known)) if os.path.exists(known) else {}
    if path not in authors:
        url = "%scommits?path=%s&per_page=100" % (WHITELIST[1], quote(path))
        try:
            commits = json.loads(fetch(url, get))
            authors[path] = commits[-1]["commit"]["author"]["name"] if commits else None
        except (OSError, ValueError, KeyError):
            return None
        os.makedirs(cache, exist_ok=True)
        with open(known, "w") as out:
            json.dump(authors, out)
    return authors[path]


def provenance(candidate, local, cache, get):
    """What the attribution of the candidate's downloaded file *local* names: title, author, licence, licence URL and source URL."""
    declared = properties(local)
    author = (declared["CreatedBy"] or first_author(candidate["path"], cache, get)
              or "%s (history: %s%s)" % (UNRECORDED, HISTORY_URL, quote(candidate["path"])))
    return {"Title": candidate["title"], "Author": author, "Licence": declared["License"] or LICENCE,
            "LicenceURL": declared["LicenseURL"] or LICENCE_URL, "Source": RAW + candidate["path"]}


def attribution(items):
    """The attribution list as Markdown: title, author, licence with its URI and source URL of each library item, once."""
    lines = ["# Attribution", ""]
    seen = set()
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            lines.append("- %(title)s, %(author)s, %(licence)s (%(licence_url)s), %(url)s" % item)
    return "\n".join(lines) + "\n"


def calls(cache, get=None):
    """The BIM Domain's furniture calls, which answer a dict holding either `candidates`, an `error`, or the `code` that places the item."""
    get = get or http_get

    def where(argument):
        try:
            return [float(argument[k]) for k in ("x", "y", "rotation")] + [str(argument["level"])]
        except (KeyError, TypeError, ValueError):
            return None

    def find_furniture(argument):
        return {"candidates": find(str(argument.get("query") or ""), index(cache, get))}

    def place_furniture(argument):
        candidate, spot = argument.get("candidate"), where(argument)
        if not isinstance(candidate, dict) or not spot or not candidate.get("title") or not candidate.get("path"):
            return {"error": "place_furniture needs a candidate from find_furniture, and x, y, rotation and level"}
        if candidate["path"] not in index(cache, get):
            return {"error": "%s is not a file of the whitelisted FreeCAD-library" % candidate["path"]}
        local = download(candidate, cache, get)
        return {"code": "from core.equipment import place_library\nplace_library(doc, %r, %r, %r, %r, %r, %r)"
                        % (local, provenance(candidate, local, cache, get), *spot)}

    def place_parametric(argument):
        kind, spot = argument.get("kind"), where(argument)
        if kind not in SIZES:
            return {"error": "%r is not a kind with a typical size; the kinds are %s" % (kind, ", ".join(SIZES))}
        if not spot:
            return {"error": "place_parametric needs x, y, rotation and level"}
        return {"code": "from core.equipment import place_parametric\nplace_parametric(doc, %r, %r, %r, %r, %r)"
                        % (kind, *spot)}

    return {"find_furniture": find_furniture, "place_furniture": place_furniture,
            "place_parametric": place_parametric}
