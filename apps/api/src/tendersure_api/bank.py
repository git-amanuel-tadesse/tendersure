"""/api/settings and /api/bank: what the app runs with, and the history it learns from."""

import os
import shutil
import tempfile
from dataclasses import asdict
from typing import List, Optional

from flask import abort, current_app, send_file
from flask_openapi3 import APIBlueprint, FileStorage, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .jobs_api import Job
from .schemas import IngestResult
from .workspaces import Invalid, safe_name

bp = APIBlueprint("bank", __name__, url_prefix="/api", abp_tags=[Tag(name="bank")])
REPORTS = ("validation_report.xlsx", "bank.xlsx")
UPLOAD_LABEL = "browser"            # how uploaded workbooks are known in the bank (BankStore.ingest_folder)


class ModelStatus(BaseModel):
    runs: bool = Field(description="whether steps use a model by default")
    why_not: str = Field(description="why not, when they do not (e.g. no key in .env)")
    provider: str
    model: str


class Settings(BaseModel):
    version: str
    home: str = Field(description="where .env, data/ and workspaces/ live")
    model: ModelStatus


class BankStatus(BaseModel):
    path: str
    exists: bool
    workbooks: int
    sheets: int
    questions: int
    clients: List[str]
    last_ingest: Optional[str] = None


class IngestForm(BaseModel):
    files: List[FileStorage] = Field(description="the workbooks; a file's name may carry its path in the folder")


class ReportPath(BaseModel):
    name: str


@bp.get("/settings", responses={200: Settings})
def settings():
    from .app import VERSION
    return Settings(version=VERSION, home=flows.current().home,
                    model=ModelStatus(**asdict(flows.model_status()))).model_dump()


@bp.get("/bank", responses={200: BankStatus})
def bank():
    return BankStatus(**asdict(flows.bank_status())).model_dump()


def _relative(name: str) -> str:
    """A path inside the picked folder ('2025/ERCS.xlsx'), each part checked; Invalid otherwise."""
    parts = (name or "").replace("\\", "/").split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        raise Invalid(f"'{name}' is not a path inside the folder")
    for p in parts[:-1]:
        safe_name(p + ".xlsx")                      # a folder name follows the file-name rule
    if not safe_name(parts[-1]).lower().endswith(".xlsx"):
        raise Invalid(f"'{parts[-1]}': the bank takes .xlsx workbooks")
    return os.path.join(*parts)


@bp.post("/bank/ingest", responses={202: Job})
def ingest(form: IngestForm):
    """Add workbooks to the bank, as a job. Workbooks uploaded again under the same path replace
    their earlier version, as a re-ingest of the folder does."""
    store = current_app.config["WORKSPACES"]
    try:
        names = [_relative(f.filename) for f in form.files if not os.path.basename(f.filename).startswith("~$")]
    except Invalid as e:
        abort(400, description=str(e))
    if not names:
        abort(400, description="no .xlsx workbooks in the upload")
    os.makedirs(os.path.join(store.root, ".staging"), exist_ok=True)
    staging = tempfile.mkdtemp(prefix="ingest-", dir=os.path.join(store.root, ".staging"))
    for f, rel in zip([f for f in form.files if not os.path.basename(f.filename).startswith("~$")], names):
        os.makedirs(os.path.dirname(os.path.join(staging, rel)), exist_ok=True)
        f.save(os.path.join(staging, rel))

    def step():
        try:
            p = flows.current()
            return IngestResult.of(flows.ingest(staging, p.bank, p.reports, UPLOAD_LABEL)).model_dump()
        finally:
            shutil.rmtree(staging, ignore_errors=True)  # the bank keeps each workbook's bytes
    job = current_app.config["JOBS"].submit("ingest", f"Add {len(names)} workbook(s) to the bank", step)
    return Job.of(job).model_dump(), 202


@bp.get("/bank/reports/<name>")
def report(path: ReportPath):
    """validation_report.xlsx or bank.xlsx, as the last ingest wrote them."""
    p = os.path.join(flows.current().reports, path.name)
    if path.name not in REPORTS or not os.path.isfile(p):
        abort(404, description=f"no report '{path.name}' yet: add workbooks to the bank first")
    return send_file(p, as_attachment=True, download_name=path.name)
