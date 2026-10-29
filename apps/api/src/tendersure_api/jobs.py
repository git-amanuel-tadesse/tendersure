"""Jobs (spec 06 W4): the long steps, run one at a time in the background.

A step shares the bank and the learned files with every other step, so one
worker thread runs them in order; a second request waits in the queue. While
a job runs, every line the `tendersure` logger emits (the same lines the CLI
prints) is added to it, from the worker or from threads the step starts
itself; only threads busy answering an HTTP request are left out (the app
marks them with `serving`). Each job is kept as JSON in workspaces/.jobs/; a job that was queued or
running when the server stopped is `interrupted` the next time it starts.

Nothing here knows about HTTP: the routes are in jobs_api.py, and the steps'
endpoints submit their work with `Runner.submit`.
"""

import json
import logging
import os
import queue
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from tendersure import flows

QUEUED, RUNNING, DONE, FAILED, INTERRUPTED = "queued", "running", "done", "failed", "interrupted"
FINISHED = (DONE, FAILED, INTERRUPTED)

log = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str                               # e.g. "refine.propose", "generate", "ingest"
    title: str                              # what a person sees, e.g. "Refine Red Cross Draft.xlsx"
    exercise_id: Optional[str] = None
    status: str = QUEUED
    lines: List[str] = field(default_factory=list)
    error: Optional[str] = None
    result: Optional[dict] = None           # the step's result, as the API returns it
    created: str = ""
    started: Optional[str] = None
    finished: Optional[str] = None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class _Lines(logging.Handler):
    """The `tendersure` log lines, into the job that is running."""

    def __init__(self, runner: "Runner"):
        super().__init__(logging.INFO)
        self.runner = runner

    def emit(self, record):
        if record.thread not in self.runner._serving:
            self.runner._line(record.getMessage())


class Runner:
    def __init__(self, folder: str):
        self.folder = folder
        self._jobs: Dict[str, Job] = {}
        self._todo: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._serving: set = set()             # threads answering an HTTP request right now
        self._current: Optional[Job] = None
        self._load()

    # --- what the API asks ---------------------------------------------------------

    def submit(self, kind: str, title: str, fn: Callable[[], Optional[dict]],
               exercise_id: Optional[str] = None) -> Job:
        """Queue `fn` (a step; returns the result to keep, as JSON-ready data)."""
        job = Job(uuid.uuid4().hex[:12], kind, title, exercise_id, created=_now())
        with self._lock:
            self._jobs[job.id] = job
            self._save(job)
        self._start()
        self._todo.put((job.id, fn))
        return self.get(job.id)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            return Job(**{**asdict(job), "lines": list(job.lines)}) if job else None

    def list(self, exercise_id: Optional[str] = None, limit: int = 50) -> List[Job]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if exercise_id is None or j.exercise_id == exercise_id]
        jobs.sort(key=lambda j: (j.created, j.id), reverse=True)
        return [self.get(j.id) for j in jobs[:limit]]

    def wait(self, job_id: str, timeout: float = 30) -> Job:
        """Until the job has finished (tests, and steps that need the one before)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.get(job_id)
            if job is None or job.status in FINISHED:
                return job
            time.sleep(0.02)
        raise TimeoutError(f"job {job_id} still {self.get(job_id).status} after {timeout}s")

    # --- the worker ------------------------------------------------------------------

    def _start(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            flows.log.setLevel(logging.INFO)
            flows.log.addHandler(_Lines(self))
            self._worker = threading.Thread(target=self._run, name="tendersure-jobs", daemon=True)
            self._worker.start()

    def serving(self, busy: bool):
        """Mark the calling thread as answering a request (its log lines belong to no job)."""
        (self._serving.add if busy else self._serving.discard)(threading.get_ident())

    def _run(self):
        while True:
            job_id, fn = self._todo.get()
            with self._lock:
                job = self._jobs[job_id]
                job.status, job.started = RUNNING, _now()
                self._current = job
                self._save(job)
            try:
                result = fn()
                status, error = DONE, None
            except flows.FlowError as e:
                result, status, error = None, FAILED, str(e)
            except (flows.LLMError, flows.PrivacyError) as e:
                result, status, error = None, FAILED, f"model step stopped: {e}"
            except Exception as e:                      # a bug: keep the server up, say what happened
                log.error("job %s (%s) crashed:\n%s", job_id, job.kind, traceback.format_exc())
                result, status, error = None, FAILED, f"{type(e).__name__}: {e}"
            with self._lock:
                job.status, job.error, job.result, job.finished = status, error, result, _now()
                self._current = None
                self._save(job)

    def _line(self, text: str):
        with self._lock:
            job = self._current
            if job is None:
                return
            job.lines.extend(text.split("\n"))
            self._save(job)             # a step logs tens of lines, not thousands: a crash loses none

    # --- on disk -----------------------------------------------------------------------

    def _path(self, job_id: str) -> str:
        return os.path.join(self.folder, f"{job_id}.json")

    def _save(self, job: Job):
        os.makedirs(self.folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(asdict(job), fh, ensure_ascii=False)
        os.replace(tmp, self._path(job.id))

    def _load(self):
        if not os.path.isdir(self.folder):
            return
        for name in os.listdir(self.folder):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.folder, name)) as fh:
                    job = Job(**json.load(fh))
            except (ValueError, TypeError, OSError):
                continue
            if job.status not in FINISHED:              # the server stopped while it waited or ran
                job.status, job.finished = INTERRUPTED, job.finished or _now()
                job.error = job.error or "the server stopped before this job finished; run it again"
                self._save(job)
            self._jobs[job.id] = job
