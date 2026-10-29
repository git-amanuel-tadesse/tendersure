"""/api/exercises/<id>/categories: the categories table generation reads (spec 06 S4).

It is the exercise's categories.csv - written by applying the refinement
review, or imported from a CSV - edited in place. Every answer carries the
table's problems row by row (the gate rule generation applies, duplicate
numbers, unknown organization types), so a mismatch is fixed here instead of
turning up as NOT WRITTEN. A table with problems is still saved: the team
fixes it over several edits.
"""

import os
import time
from typing import List, Optional

from flask import abort, current_app, send_file
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .refine_api import CATEGORIES, _need
from .schemas import ApplyResult, Category
from .workspaces import NotFound

bp = APIBlueprint("categories", __name__, url_prefix="/api/exercises/<exercise_id>/categories",
                  abp_tags=[Tag(name="categories")])


class RowProblem(BaseModel):
    number: int
    field: str = Field(description="the column to fix")
    severity: str = Field(description="error: fix before generating; warning: worth a look")
    message: str


class Table(BaseModel):
    categories: List[Category]
    problems: List[RowProblem]
    errors: int
    updated: Optional[str] = None


class Replace(BaseModel):
    categories: List[Category]


class RowPath(BaseModel):
    exercise_id: str
    number: int


class RowChange(BaseModel):
    """Only the fields given change."""
    description: Optional[str] = None
    group: Optional[str] = None
    sub_categories: Optional[List[str]] = None
    requirements: Optional[str] = None
    eligibility: Optional[List[str]] = None
    gate_question: Optional[bool] = None
    applies_to: Optional[List[str]] = None


class Import(BaseModel):
    source: str = Field(description="'refine' (the applied review), or an uploaded CSV such as uploads/x.csv")


def _folder(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].folder(eid)
    except NotFound as e:
        abort(404, description=str(e))


def _load(folder: str) -> List:
    p = os.path.join(folder, CATEGORIES)
    return flows.load_categories(p) if os.path.exists(p) else []


def _table(folder: str, cats=None) -> dict:
    cats = _load(folder) if cats is None else cats
    p = os.path.join(folder, CATEGORIES)
    problems = [RowProblem(**vars(x)) for x in flows.check_table(cats)]
    return Table(categories=[Category.of(c) for c in cats], problems=problems,
                 errors=sum(x.severity == "error" for x in problems),
                 updated=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(p)))
                 if os.path.exists(p) else None).model_dump()


def _save(folder: str, eid: str, cats: List, keep_old: bool = False) -> dict:
    ex = current_app.config["WORKSPACES"].get(eid)
    p = os.path.join(folder, CATEGORIES)
    if keep_old and os.path.exists(p):
        old = os.path.join(folder, "history", f"categories {time.strftime('%Y%m%d-%H%M%S')}.csv")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        os.replace(p, old)
    for c in cats:
        c.client = c.client or ex.client
        flows.normalise_category(c)
    flows.save_categories(cats, p)
    return _table(folder)


@bp.get("", responses={200: Table})
def get_table(path: ExercisePath):
    return _table(_folder(path.exercise_id))


@bp.put("", responses={200: Table})
def replace_table(path: ExercisePath, body: Replace):
    """The whole table, as the browser holds it (rows added, removed or renumbered)."""
    folder = _folder(path.exercise_id)
    return _save(folder, path.exercise_id, [c.core() for c in body.categories])


@bp.patch("/<int:number>", responses={200: Table})
def change_row(path: RowPath, body: RowChange):
    folder = _folder(path.exercise_id)
    cats = _load(folder)
    row = next((c for c in cats if c.number == path.number), None)
    if row is None:
        abort(404, description=f"no category {path.number} in the table")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v if not isinstance(v, str) else v.strip())
    return _save(folder, path.exercise_id, cats)


@bp.post("/import", responses={200: Table})
def import_table(path: ExercisePath, body: Import):
    """Replace the table: from the applied refinement review, or from an uploaded CSV. The table it
    replaces is kept in history/."""
    eid = path.exercise_id
    folder = _folder(eid)
    if body.source == "refine":
        final = ApplyResult.model_validate(_need(folder, "final.json", "the refinement review has not been applied"))
        cats = [c.core() for c in final.categories]
    else:
        try:
            src = current_app.config["WORKSPACES"].resolve(eid, body.source)
        except NotFound as e:
            abort(404, description=str(e))
        if not src.lower().endswith(".csv"):
            abort(400, description="import a categories table as .csv (the categories step writes one)")
        try:
            cats = flows.load_categories(src)
        except (KeyError, ValueError) as e:
            abort(422, description=f"{os.path.basename(src)} is not a categories table ({type(e).__name__}: {e})")
    return _save(folder, eid, cats, keep_old=True)


@bp.get("/categories.csv")
def download_table(path: ExercisePath):
    p = os.path.join(_folder(path.exercise_id), CATEGORIES)
    if not os.path.exists(p):
        abort(404, description="no categories table yet: apply the refinement review or import one")
    return send_file(p, as_attachment=True, download_name=CATEGORIES)
