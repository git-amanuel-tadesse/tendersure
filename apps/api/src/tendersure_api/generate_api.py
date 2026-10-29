"""/api/exercises/<id>/generate: criteria generation (spec 02) in the browser (spec 06 S5).

Every build is a run of its own, kept:

    runs/<n>/settings.json    what was asked (categories, model, options)
    runs/<n>/result.json      sheets written, NOT WRITTEN, the Decisions items
    runs/<n>/proposals.json   the model's proposals, exactly as the reviewer saw them
    runs/<n>/answers.json     the review being answered in the browser
    runs/<n>/<prefix>.xlsx    the workbook, its _qa and _learned_review

Applying the answers is a new run with the same settings, the same proposals
(no new model call for them) and the answers - what `generate --review` does
with the QA file. Files download through /api/exercises/<id>/files/runs/<n>/...
"""

import json
import os
import tempfile
import time
from typing import Dict, List, Optional

from flask import abort, current_app
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .jobs_api import Job
from .refine_api import CATEGORIES
from .schemas import GenerateResult
from .workspaces import NotFound

bp = APIBlueprint("generate", __name__, url_prefix="/api/exercises/<exercise_id>/generate",
                  abp_tags=[Tag(name="generate")])
RUNS = "runs"


class Options(BaseModel):
    only: Optional[List[int]] = Field(None, description="category numbers; all when absent")
    use_model: Optional[bool] = Field(None, description="None: when one is configured; False: no AI step")
    ai_review: bool = Field(True, description="False: stop after the proposals (rewordings applied)")
    rebuild_existing: bool = False
    formulas: bool = False
    split_files: bool = False


class RunSettings(Options):
    review_of: Optional[int] = Field(None, description="the run whose answers this run applied")


class RunSummary(BaseModel):
    number: int
    created: str
    job_id: str
    status: str = Field(description="the job's: queued, running, done, failed or interrupted")
    settings: RunSettings
    written: int = 0
    not_written: int = 0
    open: Dict[str, int] = {}


class Run(RunSummary):
    result: Optional[GenerateResult] = None
    answers: Dict[str, str] = Field(description="the review being answered: Decisions item ID -> answer")
    error: Optional[str] = None


class Runs(BaseModel):
    runs: List[RunSummary]


class RunPath(BaseModel):
    exercise_id: str
    number: int


class Answers(BaseModel):
    answers: Dict[str, str] = Field(description="item ID -> answer in the Decisions grammar; '' clears an answer")


# --- runs on disk ---------------------------------------------------------------------

def _folder(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].folder(eid)
    except NotFound as e:
        abort(404, description=str(e))


def _run_dir(folder: str, n: int) -> str:
    return os.path.join(folder, RUNS, str(n))


def _read(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as fh:
        return json.load(fh)


def _write(path: str, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _numbers(folder: str) -> List[int]:
    d = os.path.join(folder, RUNS)
    return sorted(int(n) for n in os.listdir(d) if n.isdigit()) if os.path.isdir(d) else []


def _new_run(folder: str) -> int:
    os.makedirs(os.path.join(folder, RUNS), exist_ok=True)
    n = (_numbers(folder) or [0])[-1] + 1
    while True:
        try:
            os.mkdir(_run_dir(folder, n))           # atomic: two builds never share a run
            return n
        except FileExistsError:
            n += 1


def _summary(folder: str, n: int, full: bool = False) -> dict:
    d = _run_dir(folder, n)
    meta = _read(os.path.join(d, "run.json"))
    if meta is None:
        abort(404, description=f"no run {n}")
    job = current_app.config["JOBS"].get(meta["job_id"])
    result = _read(os.path.join(d, "result.json"))
    out = dict(number=n, created=meta["created"], job_id=meta["job_id"],
               status=job.status if job else "interrupted", settings=_read(os.path.join(d, "settings.json")))
    if result:
        out.update(written=len(result["written"]), not_written=len(result["failed"]), open=result["open"])
    if not full:
        return RunSummary(**out).model_dump()
    return Run(**out, result=result, answers=_read(os.path.join(d, "answers.json"), {}),
               error=job.error if job else None).model_dump()


def _start(eid: str, folder: str, settings: RunSettings, proposals=None, answers=None) -> dict:
    ex = current_app.config["WORKSPACES"].get(eid)
    table = os.path.join(folder, CATEGORIES)
    if not os.path.exists(table):
        abort(409, description="no categories table yet: apply the refinement review or import one")
    if not flows.bank_status().exists:
        abort(409, description="the bank is empty: add past criteria workbooks first")
    n = _new_run(folder)
    d = _run_dir(folder, n)
    _write(os.path.join(d, "settings.json"), settings.model_dump())
    out = os.path.join(d, f"{ex.prefix.rstrip('-_') or ex.client}.xlsx")

    def rel(p: Optional[str]) -> Optional[str]:
        return os.path.relpath(p, folder).replace(os.sep, "/") if p else None

    def step():
        r = flows.generate(flows.GenerateSettings(
            table, ex.client, ex.prefix, out, client_name=ex.client_name or None, only=settings.only,
            rebuild_existing=settings.rebuild_existing, formulas=settings.formulas,
            split_files=settings.split_files, ai_review=settings.ai_review,
            model=flows.Model(off=settings.use_model is False), proposals=proposals, answers=answers))
        _write(os.path.join(d, "proposals.json"), flows.read_review(r.qa)[0] if proposals is None else proposals)
        result = GenerateResult.of(r)
        result = result.model_copy(update={"files": [rel(f) for f in result.files], "qa": rel(result.qa),
                                           "learned_review": rel(result.learned_review)})
        _write(os.path.join(d, "result.json"), result.model_dump())
        return {"run": n, **result.model_dump(include={"written", "open"}), "not_written": len(r.failed)}
    title = f"Generate {ex.client}" + (f" (applying run {settings.review_of}'s answers)" if settings.review_of else "")
    job = current_app.config["JOBS"].submit("generate", title, step, exercise_id=eid)
    _write(os.path.join(d, "run.json"), {"job_id": job.id, "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    return Job.of(job).model_dump()


# --- routes ---------------------------------------------------------------------------

@bp.get("", responses={200: Runs})
def list_runs(path: ExercisePath):
    folder = _folder(path.exercise_id)
    return Runs(runs=[_summary(folder, n) for n in reversed(_numbers(folder))]).model_dump()


@bp.post("", responses={202: Job})
def generate(path: ExercisePath, body: Options):
    """A first build of the exercise's categories table, as a job (a new run)."""
    folder = _folder(path.exercise_id)
    return _start(path.exercise_id, folder, RunSettings(**body.model_dump())), 202


@bp.get("/runs/<int:number>", responses={200: Run})
def get_run(path: RunPath):
    return _summary(_folder(path.exercise_id), path.number, full=True)


@bp.patch("/runs/<int:number>/answers", responses={200: Run})
def answer(path: RunPath, body: Answers):
    """Answer Decisions items of a finished run; '' clears an answer. Nothing is built yet."""
    folder = _folder(path.exercise_id)
    d = _run_dir(folder, path.number)
    result = _read(os.path.join(d, "result.json"))
    if result is None:
        abort(409, description=f"run {path.number} has no Decisions yet (it has not finished)")
    ids = {i["id"] for i in result["items"]}
    unknown = sorted(set(body.answers) - ids)
    if unknown:
        abort(400, description=f"no such Decisions item in run {path.number}: {', '.join(unknown)}")
    answers = _read(os.path.join(d, "answers.json"), {})
    for k, v in body.answers.items():
        if v.strip():
            answers[k] = v.strip()
        else:
            answers.pop(k, None)
    _write(os.path.join(d, "answers.json"), answers)
    return _summary(folder, path.number, full=True)


@bp.post("/runs/<int:number>/apply", responses={202: Job})
def apply(path: RunPath):
    """Build again with this run's answers: same settings, same proposals (no new model call for
    them). Answers this run already carried are kept, as in its QA file."""
    folder = _folder(path.exercise_id)
    d = _run_dir(folder, path.number)
    result = _read(os.path.join(d, "result.json"))
    if result is None:
        abort(409, description=f"run {path.number} has not finished: nothing to apply")
    answers = {i["id"]: i["answer"] for i in result["items"] if i["answer"]}
    answers.update(_read(os.path.join(d, "answers.json"), {}))
    settings = RunSettings(**{**_read(os.path.join(d, "settings.json")), "review_of": path.number})
    return _start(path.exercise_id, folder, settings, _read(os.path.join(d, "proposals.json"), {}), answers), 202
