"""Exercises and their files on disk (spec 06 W3).

    workspaces/<exercise-id>/
      exercise.json      what the exercise is: client, client name, sheet code prefix
      brief.txt          the client's brief, read first by every model step (spec 07 B)
      uploads/           files the team uploaded (drafts, answered workbooks, categories)
      ...                what the steps write (refine/, runs/<n>/), added by later tasks

workspaces/ sits in the project home next to data/ and is git-ignored: it
holds client data. Nothing here knows about HTTP; the routes are in
exercises.py.
"""

import json
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import List, Optional

from tendersure import flows

ALLOWED = (".xlsx", ".csv", ".pdf")
EXERCISE_FILE = "exercise.json"
UPLOADS = "uploads"
BRIEF_FILE = "brief.txt"
MAX_BRIEF = 20000                   # characters: a page or two, not a document
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CLIENT = re.compile(r"^[A-Za-z0-9]{1,20}$")
_NAME_CHARS = re.compile(r"^[\w .,()&+'-]+$")


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


@dataclass
class Exercise:
    id: str
    name: str                       # e.g. "ERCS 2026-2029"
    client: str                     # client code, e.g. ERCS
    prefix: str                     # sheet code prefix, e.g. ERCS-ET-2026-
    client_name: str = ""           # name used inside question text, if not the code
    created: str = ""


@dataclass
class StoredFile:
    path: str                       # relative to the exercise folder, with /
    size: int
    modified: str


def slug(name: str) -> str:
    """'ERCS 2026–2029' -> 'ercs-2026-2029': every run of other characters (dashes of any kind) is one '-'."""
    text = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch))
    text = re.sub(r"[\W_]+", "-", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"-+", "-", text).strip("-")[:48].strip("-") or "exercise"


def safe_name(name: str) -> str:
    """An uploaded file's name as given, or Invalid: no folders, no hidden files, a known type.

    A name with a path in it is refused rather than cut down to its last part, so a
    client's file never lands under a name the team did not choose."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not name or name != os.path.basename(name) or "\\" in name or "/" in name:
        raise Invalid(f"'{name}' is not a plain file name")
    if name.startswith(".") or ".." in name:
        raise Invalid(f"'{name}': hidden files and '..' are not accepted")
    if not _NAME_CHARS.match(name) or len(name) > 150:
        raise Invalid(f"'{name}': use letters, digits, spaces and . , ( ) & + ' - in file names")
    if not name.lower().endswith(ALLOWED):
        raise Invalid(f"'{name}': only {', '.join(ALLOWED)} files are accepted")
    return name


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write_json(path: str, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)               # a crash never leaves half a file


class Workspaces:
    def __init__(self, root: Optional[str] = None):
        self.root = os.path.realpath(root or os.path.join(flows.current().home, "workspaces"))

    # --- exercises ---------------------------------------------------------------

    def folder(self, exercise_id: str) -> str:
        if not _ID.match(exercise_id or ""):
            raise NotFound(f"no exercise '{exercise_id}'")
        d = os.path.join(self.root, exercise_id)
        if not os.path.isfile(os.path.join(d, EXERCISE_FILE)):
            raise NotFound(f"no exercise '{exercise_id}'")
        return d

    def create(self, name: str, client: str, prefix: str, client_name: str = "") -> Exercise:
        name, client, prefix = (name or "").strip(), (client or "").strip(), (prefix or "").strip()
        if not name:
            raise Invalid("give the exercise a name, e.g. 'ERCS 2026-2029'")
        if not _CLIENT.match(client):
            raise Invalid(f"client code '{client}': letters and digits only, e.g. ERCS")
        if not prefix or not re.match(r"^[A-Za-z0-9_-]+$", prefix):
            raise Invalid(f"sheet code prefix '{prefix}': letters, digits, - and _, e.g. ERCS-ET-2026-")
        os.makedirs(self.root, exist_ok=True)
        base, n = slug(name), 1
        while True:
            eid = base if n == 1 else f"{base}-{n}"
            try:
                os.mkdir(os.path.join(self.root, eid))       # atomic: two creates never share a folder
                break
            except FileExistsError:
                n += 1
        ex = Exercise(eid, name, client.upper(), prefix, client_name.strip(), _now())
        _write_json(os.path.join(self.root, eid, EXERCISE_FILE), asdict(ex))
        return ex

    def get(self, exercise_id: str) -> Exercise:
        with open(os.path.join(self.folder(exercise_id), EXERCISE_FILE)) as fh:
            return Exercise(**json.load(fh))

    def list(self) -> List[Exercise]:
        if not os.path.isdir(self.root):
            return []
        out = []
        for eid in sorted(os.listdir(self.root)):
            try:
                out.append(self.get(eid))
            except (NotFound, ValueError, TypeError):
                continue                                     # not an exercise folder
        return sorted(out, key=lambda e: e.created, reverse=True)

    def brief(self, exercise_id: str) -> str:
        p = os.path.join(self.folder(exercise_id), BRIEF_FILE)
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as fh:
            return fh.read()

    def set_brief(self, exercise_id: str, text: str) -> str:
        """The brief for the exercise's next runs; empty text removes it."""
        text = (text or "").strip()
        if len(text) > MAX_BRIEF:
            raise Invalid(f"the brief has {len(text)} characters; keep it under {MAX_BRIEF}")
        p = os.path.join(self.folder(exercise_id), BRIEF_FILE)
        if not text:
            if os.path.exists(p):
                os.remove(p)
            return ""
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return text

    # --- files ---------------------------------------------------------------------

    def resolve(self, exercise_id: str, rel: str) -> str:
        """The absolute path of a file inside the exercise, or NotFound - never anything outside it."""
        d = os.path.realpath(self.folder(exercise_id))
        p = os.path.realpath(os.path.join(d, rel or ""))
        if os.path.commonpath([d, p]) != d or p == d or os.path.basename(p) == EXERCISE_FILE \
                or not os.path.isfile(p):
            raise NotFound(f"no file '{rel}' in exercise '{exercise_id}'")
        return p

    def save_upload(self, exercise_id: str, name: str, stream) -> StoredFile:
        """Store an uploaded file under uploads/; an existing name gets ' (2)', ' (3)'..."""
        name = safe_name(name)
        d = os.path.join(self.folder(exercise_id), UPLOADS)
        os.makedirs(d, exist_ok=True)
        stem, ext = os.path.splitext(name)
        n, target = 1, name
        while os.path.exists(os.path.join(d, target)):
            n += 1
            target = f"{stem} ({n}){ext}"
        with open(os.path.join(d, target), "xb") as fh:
            while True:
                chunk = stream.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        return self._entry(exercise_id, os.path.join(UPLOADS, target))

    def files(self, exercise_id: str) -> List[StoredFile]:
        d = self.folder(exercise_id)
        out = []
        for base, dirs, names in os.walk(d):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for n in sorted(names):
                rel = os.path.relpath(os.path.join(base, n), d)
                if rel != EXERCISE_FILE and not n.startswith((".", "~$")):
                    out.append(self._entry(exercise_id, rel))
        return out

    def _entry(self, exercise_id: str, rel: str) -> StoredFile:
        st = os.stat(os.path.join(self.folder(exercise_id), rel))
        return StoredFile(rel.replace(os.sep, "/"), st.st_size, time.strftime("%Y-%m-%dT%H:%M:%S",
                                                                               time.localtime(st.st_mtime)))
