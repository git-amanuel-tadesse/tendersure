"""/api/exercises/<id>/refine/list: the category list shaped by the team's comments (spec 21).

The exercise's refine/ folder holds it:

    refine/list.json         the list now: version, rows, the draft it started from, the decided comments
    refine/versions/<n>.json each version's rows, for undo
    refine/pending.json      the one comment waiting for a decision (or being read)
    refine/comments.jsonl    every comment, the AI's answers as they came, each decision
    refine/categories.json   what "make it the categories table" last wrote, so the team's later edits stay

One comment waits at a time; a comment read on an older version of the list is read again before it can be
applied; only the latest applied comment can be undone.
"""

import json
import os
import shutil
import threading
import time
import uuid
from typing import Dict, List, Optional

from flask import abort, current_app, send_file
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .jobs_api import Job
from .schemas import Category, Draft
from .workspaces import NotFound

bp = APIBlueprint("list", __name__, url_prefix="/api/exercises/<exercise_id>/refine/list",
                  abp_tags=[Tag(name="list")])
_LOCK = threading.RLock()               # one change to a list's files at a time (re-entrant: _list may restart)
CATEGORIES = "categories.csv"


# --- shapes --------------------------------------------------------------------------------

class ListRow(BaseModel):
    key: str
    group: str
    description: str
    subs: List[str] = []
    from_draft: List[str] = Field([], description="the client's draft rows the category comes from")


class ListChange(BaseModel):
    kind: str = Field(description="reword, group, remove, fold, subs, add, split, order or merge")
    id: str = ""
    key: str = ""
    keys: List[str] = []
    target: str = ""
    text: str = ""
    group: str = ""
    subs: List[str] = []
    texts: List[str] = []
    groups: List[str] = []
    why: str = ""
    instruction: int = -1
    source: str = ""
    source_text: str = ""
    risky: str = Field("", description="why to look before applying; a flagged change starts unticked")
    ticked: bool = Field(True, description="in a decided comment: whether it was applied")


class ListInstruction(BaseModel):
    text: str
    status: str = Field(description="done, nothing_to_change, partly_done or unclear")
    note: str = ""
    targets: List[str] = []


class Comment(BaseModel):
    id: str
    author: str
    text: str
    at: str
    state: str = Field(description="reading, ready or failed while waiting; applied, rejected or undone once decided")
    list_version: int = Field(description="the version of the list it was read on")
    instructions: List[ListInstruction] = []
    changes: List[ListChange] = []
    note: str = ""
    sources: List[str] = Field([], description="the approved lists the AI was given")
    job: str = ""
    decided_at: str = ""
    version_after: int = 0


class ListState(BaseModel):
    version: int
    rows: List[ListRow]
    file: str = Field(description="the draft it started from")
    sheets: List[str] = []
    updated: str
    thread: List[Comment] = Field(description="decided comments, oldest first")
    pending: Optional[Comment] = None


class CommentIn(BaseModel):
    text: str
    author: str = Field("", description="who writes it, e.g. a team member's name")


class DecideIn(BaseModel):
    id: str
    apply: bool = Field(description="true: apply the ticked changes; false: reject the comment")
    ticked: List[str] = Field([], description="the ids of the changes to apply")


class RereadIn(BaseModel):
    id: str


class CheckIn(BaseModel):
    file: str = Field(description="an uploaded list the team made")
    sheets: List[str] = []


class Pair(BaseModel):
    target: str
    ours: str
    score: float


class GroupDiff(BaseModel):
    category: str
    target: str
    ours: str


class CheckOut(BaseModel):
    target: int
    ours: int
    same_wording: int
    close_wording: int
    same_group: int
    missing: List[str]
    extra: List[str]
    close_pairs: List[Pair]
    group_differs: List[GroupDiff]


class CategoriesIn(BaseModel):
    version: Optional[str] = Field(None, description="a label for this version of the list")


class CategoriesOut(BaseModel):
    count: int
    kept: List[str] = Field([], description="the team's edits on the table that were kept")


# --- files ---------------------------------------------------------------------------------

def _ws():
    return current_app.config["WORKSPACES"]


def _folder(eid: str) -> str:
    try:
        return _ws().folder(eid)
    except NotFound as e:
        abort(404, description=str(e))


def _path(folder: str, *name: str) -> str:
    return os.path.join(folder, "refine", *name)


def _read(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def _write(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _log(folder: str, entry: dict):
    os.makedirs(_path(folder), exist_ok=True)
    with open(_path(folder, "comments.jsonl"), "a") as fh:
        fh.write(json.dumps({"at": _now(), **entry}, ensure_ascii=False) + "\n")


def _rows(data: dict) -> List:
    return [flows.ListRow(**r) for r in data["rows"]]


def _save_version(folder: str, data: dict):
    _write(_path(folder, "versions", f"{data['version']}.json"), {"rows": data["rows"]})
    data["updated"] = _now()
    _write(_path(folder, "list.json"), data)


def restart(folder: str, drafts, file: str, sheets: List[str]):
    """A new list from the draft as written (spec 21 A); an earlier list, its versions and comments move to
    refine/history/<time>/list/."""
    with _LOCK:
        old = [n for n in ("list.json", "pending.json", "comments.jsonl", "categories.json", "versions")
               if os.path.exists(_path(folder, n))]
        if old:
            keep = _path(folder, "history", time.strftime("%Y%m%d-%H%M%S"), "list")
            os.makedirs(keep, exist_ok=True)
            for n in old:
                shutil.move(_path(folder, n), os.path.join(keep, n))
        data = {"version": 1, "rows": [vars(r) for r in flows.start_list(drafts)], "file": file,
                "sheets": list(sheets), "thread": []}
        _save_version(folder, data)
        _log(folder, {"event": "start", "file": file, "sheets": list(sheets), "rows": len(data["rows"])})


def _draft(folder: str) -> Optional[dict]:
    d = _read(_path(folder, "draft.json"))
    if d and "sheets" not in d:
        d["sheets"] = [d["sheet"]] if d.get("sheet") else []
    return d


def _drafts(folder: str):
    d = _draft(folder)
    return [Draft(**x).core() for x in d["drafts"]] if d else []


def _list(folder: str) -> dict:
    """The list; an exercise whose draft was read before lists existed starts one from it."""
    data = _read(_path(folder, "list.json"))
    if data is None:
        d = _draft(folder)
        if not d:
            abort(409, description="read the client's draft first")
        restart(folder, _drafts(folder), d["file"], d["sheets"])
        data = _read(_path(folder, "list.json"))
    return data


def _state(folder: str) -> dict:
    data = _list(folder)
    return ListState(version=data["version"], rows=data["rows"], file=data.get("file", ""),
                     sheets=data.get("sheets", []), updated=data.get("updated", ""), thread=data.get("thread", []),
                     pending=_read(_path(folder, "pending.json"))).model_dump()


def _exercise(eid: str):
    try:
        return _ws().get(eid)
    except NotFound as e:
        abort(404, description=str(e))


# --- reading a comment -----------------------------------------------------------------------

def _reading_step(eid: str, folder: str, comment_id: str):
    """The job: read the waiting comment on the list as it is now, and keep the answer with it."""
    ex = _ws().get(eid)
    brief = _ws().brief(eid)
    tables = [os.path.join(_ws().folder(e.id), CATEGORIES) for e in _ws().list() if e.id != eid]

    def step():
        data = _read(_path(folder, "list.json"))
        pending = _read(_path(folder, "pending.json"))
        if not pending or pending["id"] != comment_id:
            return {"skipped": "the comment is no longer waiting"}
        refs = flows.approved_lists(tables, exclude=ex.client)
        try:
            got = flows.read_comment(pending["text"], _rows(data), _drafts(folder), refs, ex.client, brief)
        except flows.FlowError as e:
            pending.update(state="failed", note=str(e))
            _write(_path(folder, "pending.json"), pending)
            _log(folder, {"event": "read", "id": comment_id, "error": str(e)})
            raise
        pending.update(state="ready", list_version=data["version"], note=got.note, sources=[r.client for r in refs],
                       instructions=[vars(i) for i in got.instructions],
                       changes=[dict(vars(c), ticked=not c.risky) for c in got.changes])
        with _LOCK:
            now = _read(_path(folder, "pending.json"))
            if now and now["id"] == comment_id:
                _write(_path(folder, "pending.json"), pending)
        _log(folder, {"event": "read", "id": comment_id, "text": pending["text"], "list_version": data["version"],
                      "raw": got.raw, "instructions": pending["instructions"], "changes": pending["changes"],
                      "note": got.note, "sources": pending["sources"]})
        return {"id": comment_id, "instructions": len(got.instructions), "changes": len(got.changes),
                "flagged": sum(bool(c.risky) for c in got.changes)}
    return step


def _submit(eid: str, folder: str, pending: dict):
    job = current_app.config["JOBS"].submit("list.comment", f"Read “{pending['text'][:60]}”",
                                            _reading_step(eid, folder, pending["id"]), exercise_id=eid)
    pending["job"] = job.id
    _write(_path(folder, "pending.json"), pending)
    return job


# --- routes --------------------------------------------------------------------------------------

@bp.get("", responses={200: ListState})
def get_list(path: ExercisePath):
    return _state(_folder(path.exercise_id))


@bp.post("/comment", responses={202: Job})
def comment(path: ExercisePath, body: CommentIn):
    """A team member's comment, read by the AI as a job; it then waits for Apply or Reject."""
    eid = path.exercise_id
    folder = _folder(eid)
    if not body.text.strip():
        abort(400, description="write a comment first")
    with _LOCK:
        data = _list(folder)
        waiting = _read(_path(folder, "pending.json"))
        if waiting:
            abort(409, description=f"decide on the last comment first: “{waiting['text'][:80]}” is waiting")
        pending = Comment(id=uuid.uuid4().hex[:8], author=body.author.strip() or "team", text=body.text.strip(),
                          at=_now(), state="reading", list_version=data["version"]).model_dump()
        _write(_path(folder, "pending.json"), pending)
    _log(folder, {"event": "comment", "id": pending["id"], "author": pending["author"], "text": pending["text"]})
    return Job.of(_submit(eid, folder, pending)).model_dump(), 202


@bp.post("/reread", responses={202: Job})
def reread(path: ExercisePath, body: RereadIn):
    """Read the waiting comment again, on the list as it is now (after another comment changed it)."""
    eid = path.exercise_id
    folder = _folder(eid)
    with _LOCK:
        pending = _read(_path(folder, "pending.json"))
        if not pending or pending["id"] != body.id:
            abort(404, description="no such comment is waiting")
        if pending["state"] == "reading":
            abort(409, description="the comment is being read")
        pending.update(state="reading", instructions=[], changes=[], note="")
        _write(_path(folder, "pending.json"), pending)
    return Job.of(_submit(eid, folder, pending)).model_dump(), 202


@bp.post("/decide", responses={200: ListState})
def decide(path: ExercisePath, body: DecideIn):
    """Apply the ticked changes of the waiting comment (a new version of the list), or reject it."""
    folder = _folder(path.exercise_id)
    with _LOCK:
        data = _list(folder)
        pending = _read(_path(folder, "pending.json"))
        if not pending or pending["id"] != body.id:
            abort(404, description="no such comment is waiting")
        if pending["state"] == "reading":
            abort(409, description="the comment is still being read")
        if body.apply:
            if pending["state"] != "ready":
                abort(409, description="this comment has no changes to apply: reject it, or read it again")
            if pending["list_version"] != data["version"]:
                abort(409, description="the list changed since this comment was read: read it again first")
            ticked = set(body.ticked)
            for c in pending["changes"]:
                c["ticked"] = c["id"] in ticked
            changes = [flows.ListChange(**{k: v for k, v in c.items() if k != "ticked"})
                       for c in pending["changes"] if c["ticked"]]
            data["rows"] = [vars(r) for r in flows.apply_changes(_rows(data), changes)]
            data["version"] += 1
            pending.update(state="applied", decided_at=_now(), version_after=data["version"])
        else:
            pending.update(state="rejected", decided_at=_now(), version_after=data["version"])
        data.setdefault("thread", []).append(pending)
        _save_version(folder, data)
        os.remove(_path(folder, "pending.json"))
    _log(folder, {"event": pending["state"], "id": pending["id"], "version": data["version"],
                  "ticked": [c["id"] for c in pending["changes"] if c.get("ticked")] if body.apply else []})
    return _state(folder)


@bp.post("/undo", responses={200: ListState})
def undo(path: ExercisePath):
    """Undo the latest applied comment: the list goes back to the version before it (as a new version)."""
    folder = _folder(path.exercise_id)
    with _LOCK:
        data = _list(folder)
        last = next((c for c in reversed(data.get("thread", [])) if c["state"] == "applied"), None)
        if not last or last["version_after"] != data["version"]:
            abort(409, description="only the latest applied comment can be undone")
        before = _read(_path(folder, "versions", f"{last['list_version']}.json"))
        if before is None:
            abort(409, description="the version before it is missing")
        data["rows"] = before["rows"]
        data["version"] += 1
        last.update(state="undone")
        _save_version(folder, data)
    _log(folder, {"event": "undone", "id": last["id"], "version": data["version"]})
    return _state(folder)


@bp.post("/check", responses={200: CheckOut})
def check(path: ExercisePath, body: CheckIn):
    """How close the list is to a list the team made (spec 21 F)."""
    eid = path.exercise_id
    folder = _folder(eid)
    try:
        src = _ws().resolve(eid, body.file)
    except NotFound as e:
        abort(404, description=str(e))
    try:
        got = flows.check_list(_rows(_list(folder)), src, body.sheets)
    except flows.FlowError as e:
        abort(422, description=str(e))
    return CheckOut(**got).model_dump()


@bp.get("/client.xlsx")
def client_workbook(path: ExercisePath):
    """The list in the client's layout, to send for review."""
    folder = _folder(path.exercise_id)
    data = _list(folder)
    name = os.path.splitext(os.path.basename(data.get("file") or "Categories"))[0] + " - categories.xlsx"
    out = _path(folder, name)
    flows.export_client_list(out, _rows(data))
    return send_file(out, as_attachment=True, download_name=name)


def categories_from_list(eid: str, version: Optional[str] = None) -> List:
    """The exercise's category list as categories-table rows (core Category)."""
    folder = _folder(eid)
    data = _list(folder)
    return flows.list_categories(_rows(data), _drafts(folder), _exercise(eid).client,
                                 version or f"v{data['version']}")


@bp.post("/categories", responses={200: CategoriesOut})
def make_categories(path: ExercisePath, body: CategoriesIn):
    """The list as the exercise's categories table, the input of criteria generation; edits the team made on
    the table since the last time stay."""
    eid = path.exercise_id
    folder = _folder(eid)
    data = _list(folder)
    cats = categories_from_list(eid, body.version)
    table = os.path.join(folder, CATEGORIES)
    before = _read(_path(folder, "categories.json"))
    kept: List[str] = []
    if before and os.path.exists(table):
        applied = [Category(**c).core() for c in before]
        kept = flows.keep_team_edits(cats, applied, flows.load_categories(table))
    if os.path.exists(table):
        os.makedirs(os.path.join(folder, "history"), exist_ok=True)
        shutil.copy(table, os.path.join(folder, "history", f"categories {time.strftime('%Y%m%d-%H%M%S')}.csv"))
    flows.save_categories(cats, table)
    _write(_path(folder, "categories.json"), [Category.of(c).model_dump() for c in cats])
    _log(folder, {"event": "categories", "version": data["version"], "count": len(cats)})
    return CategoriesOut(count=len(cats), kept=kept).model_dump()
