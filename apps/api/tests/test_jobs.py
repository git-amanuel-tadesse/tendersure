"""Spec 06 W4: one job at a time, its lines readable while it runs, kept on disk, interrupted by a restart."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import openpyxl
import pytest

from tendersure import flows
from tendersure_api.jobs import DONE, FAILED, INTERRUPTED, QUEUED, RUNNING, Runner


def _until(cond, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


@pytest.fixture
def runner(tmp_path):
    return Runner(str(tmp_path / ".jobs"))


def _blocking(release, lines=("step 1",), after=("step 2",), result=None):
    def step():
        for x in lines:
            flows.log.info(x)
        release.wait(5)
        for x in after:
            flows.log.info(x)
        return result
    return step


def test_a_jobs_lines_are_readable_while_it_runs_and_kept_on_disk(runner, tmp_path):
    go = threading.Event()
    job = runner.submit("test", "A test job", _blocking(go, result={"n": 2}), exercise_id="ercs")
    _until(lambda: runner.get(job.id).lines == ["step 1"])
    assert runner.get(job.id).status == RUNNING
    on_disk = json.loads((tmp_path / ".jobs" / f"{job.id}.json").read_text())
    assert on_disk["status"] == RUNNING
    go.set()
    done = runner.wait(job.id)
    assert (done.status, done.lines, done.result, done.error) == (DONE, ["step 1", "step 2"], {"n": 2}, None)
    on_disk = json.loads((tmp_path / ".jobs" / f"{job.id}.json").read_text())
    assert on_disk["status"] == DONE and on_disk["lines"] == ["step 1", "step 2"] and on_disk["finished"]


def test_lines_from_the_steps_own_threads_count_and_a_requests_do_not(runner):
    go = threading.Event()

    def step():
        with ThreadPoolExecutor(4) as pool:                 # e.g. research resolving links in parallel
            list(pool.map(lambda i: flows.log.info(f"link {i}"), range(3)))
        go.wait(5)
    job = runner.submit("test", "threads", step)
    _until(lambda: len(runner.get(job.id).lines) == 3)

    def request():                                          # a request answered while the job runs
        runner.serving(True)
        flows.log.info("a request's own line")
        runner.serving(False)
    t = threading.Thread(target=request)
    t.start()
    t.join()
    go.set()
    assert sorted(runner.wait(job.id).lines) == ["link 0", "link 1", "link 2"]


def test_jobs_run_one_at_a_time_in_order(runner):
    go = threading.Event()
    first = runner.submit("test", "first", _blocking(go))
    second = runner.submit("test", "second", lambda: flows.log.info("second ran"))
    _until(lambda: runner.get(first.id).status == RUNNING)
    time.sleep(0.1)
    assert runner.get(second.id).status == QUEUED            # the bank and learned files are shared
    go.set()
    assert runner.wait(second.id).lines == ["second ran"]
    assert runner.get(first.id).finished <= runner.get(second.id).started


def test_a_failed_job_says_why_and_the_next_one_still_runs(runner):
    def cannot():
        raise flows.FlowError("no bank at data/bank.sqlite")

    def bug():
        raise KeyError("oops")
    a, b = runner.submit("test", "a", cannot), runner.submit("test", "b", bug)
    c = runner.submit("test", "c", lambda: {"ok": True})
    assert (runner.wait(a.id).status, runner.wait(a.id).error) == (FAILED, "no bank at data/bank.sqlite")
    assert runner.wait(b.id).status == FAILED and runner.wait(b.id).error == "KeyError: 'oops'"
    assert runner.wait(c.id).result == {"ok": True}


def test_a_job_running_or_queued_when_the_server_stopped_is_interrupted(runner, tmp_path):
    go = threading.Event()
    running = runner.submit("test", "running", _blocking(go))
    queued = runner.submit("test", "queued", lambda: None)
    _until(lambda: runner.get(running.id).lines == ["step 1"])         # running, and its first line saved
    restarted = Runner(str(tmp_path / ".jobs"))              # a new server reads what the old one left
    for j in (running, queued):
        again = restarted.get(j.id)
        assert again.status == INTERRUPTED and "run it again" in again.error
    assert restarted.get(running.id).lines == ["step 1"]
    go.set()
    runner.wait(queued.id)


def test_a_real_step_reports_through_its_job(runner, tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B3"], ws["C3"] = "No", "Category Description"
    for i, (n, t) in enumerate([(1, "Supply of office stationery"), (2, "Provision of travel agency services")], 4):
        ws.cell(i, 2, n)
        ws.cell(i, 3, t)
    wb.save(tmp_path / "d.xlsx")
    job = runner.submit("refine.propose", "Refine d.xlsx", lambda: {"proposed": flows.propose_file(
        str(tmp_path / "d.xlsx"), "AAA", out=str(tmp_path / "rev.xlsx"), bank=str(tmp_path / "none"),
        research=str(tmp_path / "r.csv"), model=flows.Model(off=True)).proposed})
    done = runner.wait(job.id)
    assert done.status == DONE and done.result == {"proposed": 2}
    assert any(x.startswith("refine: 2 draft rows -> 2 proposed categories") for x in done.lines)


def test_the_api_shows_jobs(client):
    jobs = client.application.config["JOBS"]
    a = jobs.submit("test", "mine", lambda: (flows.log.info("hello"), {"n": 1})[1], exercise_id="ercs")
    jobs.submit("test", "other", lambda: None, exercise_id="nrc")
    jobs.wait(a.id)
    r = client.get(f"/api/jobs/{a.id}")
    assert r.status_code == 200 and r.json["lines"] == ["hello"] and r.json["result"] == {"n": 1}
    listed = client.get("/api/jobs?exercise_id=ercs").json["jobs"]
    assert [j["title"] for j in listed] == ["mine"] and listed[0]["lines"] == []    # read the job for its lines
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs?limit=0").status_code == 422
