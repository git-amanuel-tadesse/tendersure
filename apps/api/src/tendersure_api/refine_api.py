"""/api/exercises/<id>/refine: the client's draft category list (spec 18 A-C), read from an uploaded file.

Reading a draft starts the category list from it, exactly as written; the list and its comments are
/refine/list (list_api, spec 21). The exercise's refine/ folder holds:

    refine/draft.json      the draft: the uploaded file, its tabs, its rows
    refine/history/<when>/ what a new draft replaced
"""

import json
import os
import tempfile
import time
from typing import List, Optional

from flask import abort, current_app
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .list_api import restart
from .schemas import Draft
from .workspaces import NotFound

bp = APIBlueprint("refine", __name__, url_prefix="/api/exercises/<exercise_id>/refine",
                  abp_tags=[Tag(name="refine")])
# files of the refinement before spec 21 (an AI proposal and its review): moved to history by a new draft
_EARLIER = ("revised.json", "final.json", "previous.json")


class DraftState(BaseModel):
    file: str = Field(description="the uploaded file, e.g. uploads/Red Cross Draft.xlsx")
    sheets: List[str] = Field([], description="the tabs read, as one draft; none: the first with a list")
    tabs: List[str] = Field(description="the workbook's visible tabs, to pick the ones with the list")
    drafts: List[Draft]


class RefineState(BaseModel):
    draft: Optional[DraftState] = None


class ChooseDraft(BaseModel):
    file: str
    sheets: List[str] = Field([], description="the tabs to read as one draft; none: the first with a list")


def _folder(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].folder(eid)
    except NotFound as e:
        abort(404, description=str(e))


def _file(folder: str, name: str) -> str:
    return os.path.join(folder, "refine", name)


def _write(folder: str, name: str, data: dict):
    d = os.path.dirname(_file(folder, name))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, _file(folder, name))


def _draft_state(folder: str) -> Optional[dict]:
    """draft.json; one written before several tabs could be read has `sheet`."""
    p = _file(folder, "draft.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        d = json.load(fh)
    if "sheets" not in d:
        d["sheets"] = [d["sheet"]] if d.get("sheet") else []
    return d


def _state(folder: str) -> dict:
    return RefineState(draft=_draft_state(folder)).model_dump()


def _exercise(eid: str):
    try:
        return current_app.config["WORKSPACES"].get(eid)
    except NotFound as e:
        abort(404, description=str(e))


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


def _known_tabs(src: str, sheets: List[str], tabs: List[str]):
    missing = [s for s in sheets if s not in tabs]
    if missing:
        abort(422, description=f"{os.path.basename(src)} has no visible tab "
                               f"{', '.join(repr(s) for s in missing)} (it has: {', '.join(tabs)})")


@bp.get("", responses={200: RefineState})
def get_state(path: ExercisePath):
    return _state(_folder(path.exercise_id))


@bp.post("/draft", responses={200: RefineState})
def choose_draft(path: ExercisePath, body: ChooseDraft):
    """Read the draft from an uploaded file (one tab, or several as one draft). The category list starts
    again from it, exactly as written; the earlier list and its comments move to refine/history/."""
    eid = path.exercise_id
    folder = _folder(eid)
    src = _upload(eid, body.file)
    tabs = _flow(flows.draft_tabs, src)
    sheets = list(dict.fromkeys(s for s in body.sheets if s))
    _known_tabs(src, sheets, tabs)
    drafts = _flow(flows.read_draft, src, sheets)
    earlier = [n for n in ("draft.json",) + _EARLIER if os.path.exists(_file(folder, n))]
    if any(n != "draft.json" for n in earlier):
        keep = _file(folder, os.path.join("history", time.strftime("%Y%m%d-%H%M%S")))
        os.makedirs(keep, exist_ok=True)
        for n in earlier:
            os.replace(_file(folder, n), os.path.join(keep, n))
    _write(folder, "draft.json", DraftState(file=body.file, sheets=sheets, tabs=tabs,
                                         drafts=[Draft.of(d) for d in drafts]).model_dump())
    restart(folder, drafts, body.file, sheets)
    return _state(folder)
