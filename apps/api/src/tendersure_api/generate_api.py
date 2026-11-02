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
FINAL = "final.json"


class Options(BaseModel):
    only: Optional[List[int]] = Field(None, description="category numbers; all when absent")
    use_model: Optional[bool] = Field(None, description="None: when one is configured; False: no AI step")
    ai_review: bool = Field(True, description="False: stop after the proposals (rewordings applied)")
    rebuild_existing: bool = False
    formulas: bool = True
    split_files: bool = False


class RunSettings(Options):
    review_of: Optional[int] = Field(None, description="the run whose answers this run applied")
    brief: str = Field("", description="the exercise's brief when the run started (spec 07 B)")
    benchmark: Optional[str] = Field(None, description="the benchmark workbook the run started from (spec 08)")


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
    final: Optional[int] = Field(None, description="the run marked as the exercise's final version (spec 11)")


class Final(BaseModel):
    run: int
    file: str = Field(description="its workbook, relative to the exercise")
    at: str


class FinishStep(BaseModel):
    file: Optional[str] = Field(None, description="an uploaded workbook (the team's edited copy); "
                                                  "none: this run's own workbook")


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
    from .benchmark_api import chosen, settings_for
    bench = settings_for(eid, folder)
    settings = settings.model_copy(update={"brief": current_app.config["WORKSPACES"].brief(eid),
                                           "benchmark": chosen(folder)})
    _write(os.path.join(d, "settings.json"), settings.model_dump())
    out = os.path.join(d, f"{ex.prefix.rstrip('-_') or ex.client}.xlsx")

    def rel(p: Optional[str]) -> Optional[str]:
        return os.path.relpath(p, folder).replace(os.sep, "/") if p else None

    def step():
        r = flows.generate(flows.GenerateSettings(
            table, ex.client, ex.prefix, out, client_name=ex.client_name or None, only=settings.only,
            rebuild_existing=settings.rebuild_existing, formulas=settings.formulas,
            split_files=settings.split_files, ai_review=settings.ai_review,
            model=flows.Model(off=settings.use_model is False), proposals=proposals, answers=answers,
            brief=settings.brief, **bench))
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
    final = _read(os.path.join(folder, FINAL))
    return Runs(runs=[_summary(folder, n) for n in reversed(_numbers(folder))],
                final=final["run"] if final else None).model_dump()


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
    """Answer Decisions items of a finished run; '' clears an answer (accepts the proposal), and is
    kept as such, so it also clears an answer the run carried from the one before. Nothing is built yet."""
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
        answers[k] = v.strip()                         # '' too: "as proposed" must beat a carried answer
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
    answers.update(_read(os.path.join(d, "answers.json"), {}))      # a cleared answer ('') overrides a carried one
    answers = {k: v for k, v in answers.items() if v}
    settings = RunSettings(**{**_read(os.path.join(d, "settings.json")), "review_of": path.number})
    return _start(path.exercise_id, folder, settings, _read(os.path.join(d, "proposals.json"), {}), answers), 202


# --- finishing a run (spec 11) ----------------------------------------------------------------

def _finished(folder: str, n: int) -> dict:
    result = _read(os.path.join(_run_dir(folder, n), "result.json"))
    if result is None:
        abort(409, description=f"run {n} has not finished")
    return result


def _workbook(eid: str, folder: str, n: int, body: Optional[FinishStep]) -> str:
    """The team's edited copy when one is given, else the run's own workbook."""
    if body is not None and body.file:
        try:
            return current_app.config["WORKSPACES"].resolve(eid, body.file)
        except NotFound as e:
            abort(404, description=str(e))
    return os.path.join(folder, _finished(folder, n)["files"][0])


@bp.post("/runs/<int:number>/final", responses={200: Final})
def mark_final(path: RunPath):
    """This run is the exercise's final version; marking another run moves the mark."""
    folder = _folder(path.exercise_id)
    final = Final(run=path.number, file=_finished(folder, path.number)["files"][0],
                  at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    _write(os.path.join(folder, FINAL), final.model_dump())
    return final.model_dump()


@bp.post("/runs/<int:number>/check", responses={202: Job})
def check_copy(path: RunPath, body: FinishStep):
    """Check a workbook - the team's edited copy, or the run's own - against this run's workbook
    (what changed since generation) and the categories table, as a job (spec 03)."""
    eid, n = path.exercise_id, path.number
    folder = _folder(eid)
    ex = current_app.config["WORKSPACES"].get(eid)
    target = _workbook(eid, folder, n, body)
    generated = _workbook(eid, folder, n, None)
    report = os.path.join(_run_dir(folder, n), f"check - {os.path.splitext(os.path.basename(target))[0]}.xlsx")

    def step():
        from collections import Counter
        edited = os.path.abspath(target) != os.path.abspath(generated)
        r = flows.check(target, ex.client, os.path.join(folder, CATEGORIES), generated if edited else None,
                        None, report, flows.Model(off=True))
        suggested = flows.suggest_from_edits(generated, target, ex.client) if edited else []
        return {"errors": r.errors, "warnings": r.warnings, "report": os.path.relpath(report, folder).replace(os.sep, "/"),
                "rules": dict(Counter(f.rule for f in r.findings).most_common()),
                "suggested": [{"action": x.action, "match": x.match, "value": x.value, "groups": x.groups, "note": x.note,
                               "problem": ""} for x in suggested]}
    job = current_app.config["JOBS"].submit("check", f"Check {os.path.basename(target)}", step, exercise_id=eid)
    return Job.of(job).model_dump(), 202


@bp.post("/runs/<int:number>/bank", responses={202: Job})
def add_to_bank(path: RunPath, body: FinishStep):
    """Add the team's edited copy - or this run's workbook - to the bank as the client's history,
    so later exercises start from the client's own sheets. Adding it again replaces it."""
    import shutil
    from .bank import UPLOAD_LABEL
    eid, n = path.exercise_id, path.number
    folder = _folder(eid)
    source = _workbook(eid, folder, n, body)
    store = current_app.config["WORKSPACES"]
    os.makedirs(os.path.join(store.root, ".staging"), exist_ok=True)
    staging = tempfile.mkdtemp(prefix="bank-", dir=os.path.join(store.root, ".staging"))
    os.makedirs(os.path.join(staging, eid))
    shutil.copy(source, os.path.join(staging, eid, os.path.basename(source)))      # one path per exercise and file

    def step():
        try:
            p = flows.current()
            r = flows.ingest(staging, p.bank, p.reports, UPLOAD_LABEL)
            return {"added": r.added, "replaced": r.replaced, "unchanged": r.unchanged, "sheets_added": r.sheets_added,
                    "tiers": r.tiers}
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    job = current_app.config["JOBS"].submit("ingest", f"Add {os.path.basename(source)} to the bank", step, exercise_id=eid)
    return Job.of(job).model_dump(), 202



# --- preview: a run's sheets as the client will see them (spec 16 F) ---------------------------

class SheetPath(RunPath):
    code: str = Field(description="the sheet's code, e.g. PSI-ET-2026-001")


class PreviewRow(BaseModel):
    section: str = ""
    sub_section: str = ""
    criteria: str
    marks: float = 0
    answer_type: Optional[int] = None
    options: str = ""
    applies_to: str = ""
    mandatory: bool = False


class SheetPreview(BaseModel):
    codes: List[str] = Field(description="every sheet of the run's workbook, in order")
    code: str
    title: str
    rows: List[PreviewRow]
    total: float


@bp.get("/runs/<int:number>/sheets/<code>", responses={200: SheetPreview})
def preview(path: SheetPath):
    """One sheet of the run's workbook, read back from the file as written: sections, sub-sections,
    questions, marks and Applies To."""
    folder = _folder(path.exercise_id)
    book = _workbook(path.exercise_id, folder, path.number, None)
    found = flows.preview_sheet(book, path.code)
    if found is None:
        abort(404, description=f"no sheet {path.code} in run {path.number}")
    names, name, s = found
    rows = [PreviewRow(section=r.section or "", sub_section=r.sub_section or "", criteria=r.criteria, marks=r.marks or 0,
                       answer_type=r.answer_type, options=r.options or "", applies_to=r.applies_to or "",
                       mandatory=bool(r.mandatory)) for r in (s.rows if s else [])]
    return SheetPreview(codes=names, code=name, title=(s.title if s else name) or name, rows=rows,
                        total=sum(r.marks for r in rows)).model_dump()
