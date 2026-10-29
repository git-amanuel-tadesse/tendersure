"""/api/exercises: create, list and read exercises; upload and download their files."""

from typing import List

from flask import abort, current_app, send_file
from flask_openapi3 import APIBlueprint, FileStorage, Tag
from pydantic import BaseModel, Field

from .workspaces import Invalid, NotFound, Workspaces

bp = APIBlueprint("exercises", __name__, url_prefix="/api/exercises", abp_tags=[Tag(name="exercises")])
MAX_UPLOAD = 100 * 1024 * 1024


def store() -> Workspaces:
    return current_app.config["WORKSPACES"]


class Exercise(BaseModel):
    id: str
    name: str
    client: str = Field(description="client code, e.g. ERCS")
    prefix: str = Field(description="sheet code prefix, e.g. ERCS-ET-2026-")
    client_name: str = ""
    created: str


class NewExercise(BaseModel):
    name: str = Field(description="e.g. ERCS 2026-2029")
    client: str
    prefix: str
    client_name: str = ""


class StoredFile(BaseModel):
    path: str = Field(description="relative to the exercise, e.g. uploads/Red Cross Draft.xlsx")
    size: int
    modified: str


class ExerciseDetail(Exercise):
    files: List[StoredFile]


class Exercises(BaseModel):
    exercises: List[Exercise]


class ExercisePath(BaseModel):
    exercise_id: str


class Brief(BaseModel):
    """What the client says it is looking for: read first by every model step (above past
    questionnaires; the template rules still apply). Empty when there is none."""
    text: str = ""


class FilePath(BaseModel):
    exercise_id: str
    path: str


class Upload(BaseModel):
    file: FileStorage


def _call(fn, *args):
    try:
        return fn(*args)
    except NotFound as e:
        abort(404, description=str(e))
    except Invalid as e:
        abort(400, description=str(e))


def _detail(eid: str) -> dict:
    ex = _call(store().get, eid)
    return ExerciseDetail(**vars(ex), files=[StoredFile(**vars(f)) for f in _call(store().files, eid)]).model_dump()


@bp.get("", responses={200: Exercises})
def list_exercises():
    return Exercises(exercises=[Exercise(**vars(e)) for e in store().list()]).model_dump()


@bp.post("", responses={201: ExerciseDetail})
def create_exercise(body: NewExercise):
    ex = _call(store().create, body.name, body.client, body.prefix, body.client_name)
    return _detail(ex.id), 201


@bp.get("/<exercise_id>", responses={200: ExerciseDetail})
def get_exercise(path: ExercisePath):
    return _detail(path.exercise_id)


@bp.get("/<exercise_id>/brief", responses={200: Brief})
def get_brief(path: ExercisePath):
    return Brief(text=_call(store().brief, path.exercise_id)).model_dump()


@bp.put("/<exercise_id>/brief", responses={200: Brief})
def set_brief(path: ExercisePath, body: Brief):
    """Saved for the exercise's next refinement and generation runs; empty text removes it."""
    return Brief(text=_call(store().set_brief, path.exercise_id, body.text)).model_dump()


@bp.post("/<exercise_id>/files", responses={201: StoredFile})
def upload_file(path: ExercisePath, form: Upload):
    saved = _call(store().save_upload, path.exercise_id, form.file.filename, form.file.stream)
    return StoredFile(**vars(saved)).model_dump(), 201


@bp.get("/<exercise_id>/files/<path:path>")
def download_file(path: FilePath):
    """The file's bytes, as an attachment under its own name."""
    p = _call(store().resolve, path.exercise_id, path.path)
    return send_file(p, as_attachment=True, download_name=p.rsplit("/", 1)[-1])
