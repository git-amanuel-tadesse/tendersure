"""/api/exercises/<id>/refine: category refinement (spec 01) in the browser (spec 06 S3).

The exercise's refine/ folder holds the state as JSON, the source of truth
inside the app; the workbooks the team sends to the client are written from
it on download, and an answered one can come back in:

    refine/draft.json      the draft list: the uploaded file, its tab, its rows
    refine/revised.json    the proposal, with the review's decisions and comments
    refine/final.json      the review applied
    categories.csv         the categories table generation reads (spec 06 S4)
    refine/history/<when>/ what a new draft replaced
"""

import json
import os
import shutil
import tempfile
import time
from typing import Dict, List, Optional, Tuple

from flask import abort, current_app, send_file
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .jobs_api import Job
from .schemas import ApplyResult, Category, Draft, Line, ProposeResult
from .workspaces import NotFound

bp = APIBlueprint("refine", __name__, url_prefix="/api/exercises/<exercise_id>/refine",
                  abp_tags=[Tag(name="refine")])
DECISIONS = flows.DECISIONS
_LISTS = {"split_into": " | ", "merge_with": ", "}     # answer fields holding lists, as the workbook writes them
CATEGORIES = "categories.csv"


# --- shapes --------------------------------------------------------------------------

class DraftState(BaseModel):
    file: str = Field(description="the uploaded file, e.g. uploads/Red Cross Draft.xlsx")
    sheet: Optional[str] = None
    tabs: List[str] = Field(description="the workbook's tabs, to pick the one with the list")
    drafts: List[Draft]


class Revised(BaseModel):
    lines: List[Line]
    target: Optional[List[int]] = None
    proposed: int
    statuses: Dict[str, int]
    questions: int
    created: str


class RefineState(BaseModel):
    draft: Optional[DraftState] = None
    revised: Optional[Revised] = None
    final: Optional[ApplyResult] = None


class ChooseDraft(BaseModel):
    file: str
    sheet: Optional[str] = None


class Propose(BaseModel):
    target: Optional[List[int]] = Field(None, min_length=2, max_length=2, description="[low, high], e.g. [50, 60]")
    country: str = Field("", description="ISO code for researched licences, e.g. ET")
    use_model: Optional[bool] = Field(None, description="None: when one is configured; False: rules only")


class LineChange(BaseModel):
    index: int = Field(ge=0, description="the line's position in the revised list")
    decision: Optional[str] = Field(None, description="accept, reject, remove, edit, split, merge, or '' (accept)")
    comment: Optional[str] = Field(None, description="the client's comment; with edit or merge, the new wording")
    code: Optional[str] = None
    split_into: Optional[List[str]] = Field(None, description="with split: each piece becomes a category")
    merge_with: Optional[List[str]] = Field(None, description="with merge: the # numbers (index + 1) of the lines "
                                                                "that become one category with this one")


class LineChanges(BaseModel):
    changes: List[LineChange]


class RefineImport(BaseModel):
    file: str = Field(description="an uploaded revised list with its Decision column answered")
    confirm: bool = Field(False, description="False: only list what would change")


class Change(BaseModel):
    index: int
    description: str
    field: str
    before: str
    after: str


class ImportResult(BaseModel):
    changes: List[Change]
    unmatched: List[str] = Field(description="rows of the workbook that match no line here: not applied")
    applied: bool


class RefineApply(BaseModel):
    version: Optional[str] = Field(None, description="a label for this version of the list")
    use_model: Optional[bool] = None


# --- the state on disk ---------------------------------------------------------------

def _folder(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].folder(eid)
    except NotFound as e:
        abort(404, description=str(e))


# Helpers take the exercise's folder, resolved while answering the request:
# a job's step runs in the worker thread, outside any request.

def _file(folder: str, name: str) -> str:
    return os.path.join(folder, "refine", name)


def _read(folder: str, name: str) -> Optional[dict]:
    p = _file(folder, name)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _write(folder: str, name: str, data: dict):
    d = os.path.dirname(_file(folder, name))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, _file(folder, name))


def _state(folder: str) -> dict:
    return RefineState(draft=_read(folder, "draft.json"), revised=_read(folder, "revised.json"),
                       final=_read(folder, "final.json")).model_dump()


def _exercise(eid: str):
    try:
        return current_app.config["WORKSPACES"].get(eid)
    except NotFound as e:
        abort(404, description=str(e))


def _need(folder: str, name: str, what: str) -> dict:
    data = _read(folder, name)
    if data is None:
        abort(409, description=what)
    return data


def _model(use: Optional[bool]) -> flows.Model:
    return flows.Model(off=use is False)


def _revised(folder: str) -> Tuple[List, List, Optional[Tuple[int, int]], dict]:
    data = _need(folder, "revised.json", "no revised list yet: run the proposal first")
    draft = _need(folder, "draft.json", "no draft list yet")
    drafts = [Draft(**d).core() for d in draft["drafts"]]
    lines = [Line.model_validate(ln).core() for ln in data["lines"]]
    return drafts, lines, tuple(data["target"]) if data.get("target") else None, data


def _save_revised(folder: str, lines, target, created: Optional[str] = None):
    r = flows.ProposeResult([], lines, target)
    _write(folder, "revised.json", Revised(lines=[Line.of(ln) for ln in lines], target=list(target) if target else None,
                                        proposed=r.proposed, statuses=r.statuses, questions=r.questions,
                                        created=created or time.strftime("%Y-%m-%dT%H:%M:%S")).model_dump())


def _upload(eid: str, rel: str) -> str:
    try:
        return current_app.config["WORKSPACES"].resolve(eid, rel)
    except NotFound as e:
        abort(404, description=str(e))


def _flow(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except flows.FlowError as e:
        abort(422, description=str(e))


# --- routes ---------------------------------------------------------------------------

@bp.get("", responses={200: RefineState})
def get_state(path: ExercisePath):
    return _state(_folder(path.exercise_id))


@bp.post("/draft", responses={200: RefineState})
def choose_draft(path: ExercisePath, body: ChooseDraft):
    """Read the draft list from an uploaded file. A revised or final list from an earlier draft moves
    to refine/history/: a new draft starts the refinement again."""
    eid = path.exercise_id
    folder = _folder(eid)
    src = _upload(eid, body.file)
    tabs = _flow(flows.draft_tabs, src)
    if body.sheet and body.sheet not in tabs:
        abort(422, description=f"{os.path.basename(src)} has no tab '{body.sheet}' (it has: {', '.join(tabs)})")
    drafts = _flow(flows.read_draft, src, body.sheet)
    old = [n for n in ("draft.json", "revised.json", "final.json") if os.path.exists(_file(folder, n))]
    if any(n != "draft.json" for n in old):
        keep = _file(folder, os.path.join("history", time.strftime("%Y%m%d-%H%M%S")))
        os.makedirs(keep, exist_ok=True)
        for n in old:
            os.replace(_file(folder, n), os.path.join(keep, n))
    _write(folder, "draft.json", DraftState(file=body.file, sheet=body.sheet, tabs=tabs,
                                         drafts=[Draft.of(d) for d in drafts]).model_dump())
    return _state(folder)


@bp.post("/propose", responses={202: Job})
def propose(path: ExercisePath, body: Propose):
    """Propose the revised list, as a job; its result is the proposal, also kept as the revised list."""
    eid = path.exercise_id
    folder = _folder(eid)
    ex = _exercise(eid)
    draft = _need(folder, "draft.json", "choose the draft list first")
    # read again: the reader may have learned columns since the draft was chosen
    drafts = _flow(flows.read_draft, _upload(eid, draft["file"]), draft.get("sheet"))
    _write(folder, "draft.json", {**draft, "drafts": [Draft.of(d).model_dump() for d in drafts]})
    target = tuple(body.target) if body.target else None
    if target and not 0 < target[0] <= target[1]:
        abort(400, description=f"target {list(target)}: give [low, high] with 0 < low <= high")

    brief = current_app.config["WORKSPACES"].brief(eid)

    def step():
        p = flows.current()
        lines = flows.propose(drafts, ex.client, target, p.bank, p.learned("research"), body.country,
                              _model(body.use_model), brief)
        if os.path.exists(_file(folder, "final.json")):
            os.remove(_file(folder, "final.json"))     # applied to a proposal that is no longer there
        _save_revised(folder, lines, target)
        return ProposeResult.of(flows.ProposeResult(drafts, lines, target)).model_dump()
    job = current_app.config["JOBS"].submit("refine.propose", f"Propose a revised list from {draft['file']}",
                                            step, exercise_id=eid)
    return Job.of(job).model_dump(), 202


@bp.patch("/lines", responses={200: RefineState})
def change_lines(path: ExercisePath, body: LineChanges):
    """The review: decisions, client comments and category codes, for any number of lines at once."""
    eid = path.exercise_id
    folder = _folder(eid)
    _, lines, target, data = _revised(folder)
    for ch in body.changes:
        if ch.index >= len(lines):
            abort(400, description=f"no line {ch.index}: the revised list has {len(lines)}")
        if ch.decision is not None and ch.decision.strip().lower() not in DECISIONS:
            abort(400, description=f"line {ch.index}: decision '{ch.decision}' is not one of "
                                   f"{', '.join(d for d in DECISIONS if d)}")
    for ch in body.changes:
        ln = lines[ch.index]
        if ch.decision is not None:
            ln.decision = ch.decision.strip().lower()
        if ch.comment is not None:
            ln.comment = ch.comment.strip()
        if ch.code is not None:
            ln.code = ch.code.strip()
        if ch.split_into is not None:
            ln.split_into = [x.strip() for x in ch.split_into if x.strip()]
        if ch.merge_with is not None:
            ln.merge_with = [str(x).strip().lstrip("#") for x in ch.merge_with if str(x).strip()]
    _save_revised(folder, lines, target, data["created"])
    return _state(folder)


@bp.get("/revised.xlsx")
def download_revised(path: ExercisePath):
    """The revised list as a workbook, to send to the client; written from the state as it is now."""
    eid = path.exercise_id
    folder = _folder(eid)
    ex = _exercise(eid)
    drafts, lines, target, _ = _revised(folder)
    name = os.path.splitext(os.path.basename(_read(folder, "draft.json")["file"]))[0] + " - Revised.xlsx"
    out = _file(folder, name)
    _flow(flows.export_revised, out, drafts, lines, ex.client, target)
    return send_file(out, as_attachment=True, download_name=name)


def _key(ln) -> tuple:
    return ln.status, tuple(ln.from_draft), " ".join(ln.description.lower().split())


@bp.post("/import", responses={200: ImportResult})
def import_answers(path: ExercisePath, body: RefineImport):
    """A revised list the client answered in Excel: its decisions and comments replace the browser's.
    Without `confirm`, only lists what would change."""
    eid = path.exercise_id
    folder = _folder(eid)
    drafts, lines, target, data = _revised(folder)
    their_drafts, theirs = _flow(flows.import_revised, _upload(eid, body.file))
    if [(d.id, d.description) for d in their_drafts] != [(d.id, d.description) for d in drafts]:
        abort(409, description="this revised list was made from another draft list: nothing applied")
    index = {_key(ln): i for i, ln in enumerate(lines)}
    loose: Dict[tuple, List[int]] = {}
    for i, ln in enumerate(lines):
        loose.setdefault(_key(ln)[:2], []).append(i)
    changes, unmatched = [], []
    for t in theirs:
        i = index.get(_key(t))
        if i is None and len(loose.get(_key(t)[:2], [])) == 1:
            i = loose[_key(t)[:2]][0]                 # the wording was touched; the line is still clear
        if i is None:
            unmatched.append(t.description)
            continue
        for field in ("decision", "comment", "code", *_LISTS):
            before, after = getattr(lines[i], field) or "", getattr(t, field) or ""
            if field in _LISTS:
                before, after = _LISTS[field].join(before), _LISTS[field].join(after)
            if before != after:
                changes.append(Change(index=i, description=lines[i].description, field=field, before=before,
                                      after=after))
    if body.confirm:
        for c in changes:
            value = c.after
            if c.field in _LISTS:
                value = [x.strip() for x in value.split(_LISTS[c.field].strip()) if x.strip()]
            setattr(lines[c.index], c.field, value)
        _save_revised(folder, lines, target, data["created"])
    return ImportResult(changes=changes, unmatched=unmatched, applied=body.confirm).model_dump()


@bp.post("/apply", responses={202: Job})
def apply(path: ExercisePath, body: RefineApply):
    """Apply the review, as a job: the final list, and the exercise's categories table."""
    eid = path.exercise_id
    folder = _folder(eid)
    ex = _exercise(eid)
    drafts, lines, _, _ = _revised(folder)

    def step():
        r = flows.apply(drafts, lines, ex.client, body.version, _model(body.use_model))
        r.categories_out = CATEGORIES
        table = os.path.join(folder, CATEGORIES)
        before = _read(folder, "final.json")
        if before and os.path.exists(table):            # the team's edits since the last apply stay
            applied = [Category(**c).core() for c in before.get("categories", [])]
            for line in flows.keep_team_edits(r.categories, applied, flows.load_categories(table)):
                flows.log.info(f"categories table: {line}")
            os.makedirs(os.path.join(folder, "history"), exist_ok=True)
            shutil.copy(table, os.path.join(folder, "history", f"categories {time.strftime('%Y%m%d-%H%M%S')}.csv"))
        flows.save_categories(r.categories, table)
        for p in r.problems:
            flows.log.info(f"REVIEW PROBLEM: {p}")
        flows.log.info(f"refine: {len(drafts)} draft rows -> {len(r.final)} final categories "
                       f"({len(r.excluded)} lines removed, rejected or folded); {len(r.notes)} client comments")
        out = ApplyResult.of(r).model_dump()
        _write(folder, "final.json", out)
        return out
    job = current_app.config["JOBS"].submit("refine.apply", "Apply the review", step, exercise_id=eid)
    return Job.of(job).model_dump(), 202


@bp.get("/revised-client.xlsx")
def download_revised_for_client(path: ExercisePath):
    """The proposed list as the client sees it: descriptions and sub-categories only, in its draft's layout."""
    eid = path.exercise_id
    folder = _folder(eid)
    _, lines, _, _ = _revised(folder)
    name = os.path.splitext(os.path.basename(_read(folder, "draft.json")["file"]))[0] + " - for the client.xlsx"
    out = _file(folder, name)
    flows.export_for_client(out, lines)
    return send_file(out, as_attachment=True, download_name=name)


@bp.get("/final-client.xlsx")
def download_final_for_client(path: ExercisePath):
    """The final list as the client sees it."""
    folder = _folder(path.exercise_id)
    final = ApplyResult.model_validate(_need(folder, "final.json", "the review has not been applied yet"))
    out = _file(folder, "Final Categories - for the client.xlsx")
    flows.export_for_client(out, [ln.core() for ln in final.final], "Final Categories")
    return send_file(out, as_attachment=True, download_name="Final Categories - for the client.xlsx")


@bp.get("/final.xlsx")
def download_final(path: ExercisePath):
    eid = path.exercise_id
    folder = _folder(eid)
    final = ApplyResult.model_validate(_need(folder, "final.json", "the review has not been applied yet"))
    r = flows.ApplyResult([], [ln.core() for ln in final.final], [],
                          [(n.line.core(), n.comment, n.change) for n in final.notes], final.problems)
    out = _file(folder, "Final Categories.xlsx")
    flows.export_final(out, r)
    return send_file(out, as_attachment=True, download_name="Final Categories.xlsx")

