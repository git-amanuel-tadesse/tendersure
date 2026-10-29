"""/api/jobs: what is running, what ran, and each job's lines so far.

The browser polls GET /api/jobs/<id> while a job runs (spec 06 W4)."""

from dataclasses import asdict
from typing import Any, List, Optional

from flask import abort, current_app
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from .jobs import Runner

bp = APIBlueprint("jobs", __name__, url_prefix="/api/jobs", abp_tags=[Tag(name="jobs")])


def runner() -> Runner:
    return current_app.config["JOBS"]


class Job(BaseModel):
    id: str
    kind: str = Field(description="e.g. refine.propose, generate, ingest")
    title: str
    exercise_id: Optional[str] = None
    status: str = Field(description="queued, running, done, failed or interrupted")
    lines: List[str] = Field(description="the step's report lines so far, as the CLI prints them")
    error: Optional[str] = None
    result: Optional[Any] = Field(None, description="the step's result once done; its shape depends on kind")
    created: str
    started: Optional[str] = None
    finished: Optional[str] = None

    @classmethod
    def of(cls, job) -> "Job":
        return cls(**asdict(job))


class Jobs(BaseModel):
    jobs: List[Job]


class JobQuery(BaseModel):
    exercise_id: Optional[str] = None
    limit: int = Field(50, ge=1, le=500)


class JobPath(BaseModel):
    job_id: str


@bp.get("", responses={200: Jobs})
def list_jobs(query: JobQuery):
    """Newest first. A list leaves out each job's lines and result: read the job for those."""
    jobs = [Job.of(j).model_copy(update={"lines": [], "result": None})
            for j in runner().list(query.exercise_id, query.limit)]
    return Jobs(jobs=jobs).model_dump()


@bp.get("/<job_id>", responses={200: Job})
def get_job(path: JobPath):
    job = runner().get(path.job_id)
    if job is None:
        abort(404, description=f"no job '{path.job_id}'")
    return Job.of(job).model_dump()
