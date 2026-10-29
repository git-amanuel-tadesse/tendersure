"""Spec 06 S5: generation in the browser - a run, the Decisions answered, applied as the next run."""

import io
import os

import openpyxl
import pytest

from tendersure import flows

W = {"X-TenderSure": "1"}
TABLE = [{"number": 1, "description": "Supply of Office Stationery and Toners", "group": "GENERAL"},
         {"number": 2, "description": "Supply of Cleaning Materials", "group": "GENERAL"},
         {"number": 4, "description": "Provision of Cleaning and Janitorial Services", "group": "SERVICES"}]


@pytest.fixture
def ex(client, home, corpus):
    flows.ingest(corpus, flows.current().bank, flows.current().reports)       # the bank the build reads
    eid = client.post("/api/exercises", json={"name": "AAA 2026", "client": "AAA", "prefix": "AAA-ET-2026-"},
                      headers=W).json["id"]
    assert client.put(f"/api/exercises/{eid}/categories", json={"categories": TABLE}, headers=W).status_code == 200
    return eid


def _run(client, r):
    assert r.status_code == 202, r.json
    job = client.application.config["JOBS"].wait(r.json["id"])
    assert job.status == "done", (job.error, job.lines[-5:])
    return job


def _items(run):
    return {i["id"]: i for i in run["result"]["items"]}


def _sheet(client, ex, run, code):
    data = client.get(f"/api/exercises/{ex}/files/{run['result']['files'][0]}").data
    ws = openpyxl.load_workbook(io.BytesIO(data))[code]
    return [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]


def test_answering_a_q_and_an_m_item_applies_them_in_the_next_run(client, ex):
    job = _run(client, client.post(f"/api/exercises/{ex}/generate", json={"use_model": False}, headers=W))
    assert job.result["run"] == 1 and job.result["written"] == ["AAA-ET-2026-001", "AAA-ET-2026-002", "AAA-ET-2026-004"]
    assert any(x.startswith("wrote 3 sheets") for x in job.lines)
    first = client.get(f"/api/exercises/{ex}/generate/runs/1").json
    q = next(i for i in first["result"]["items"] if i["id"].startswith("Q-"))
    m = next(i for i in first["result"]["items"] if i["id"].startswith("M-") and i["codes"] == ["AAA-ET-2026-004"])
    assert "Do you hold a valid service licence? Yes/No" in _sheet(client, ex, first, "AAA-ET-2026-004")

    r = client.patch(f"/api/exercises/{ex}/generate/runs/1/answers",
                     json={"answers": {q["id"]: "remove all", m["id"]: "company 10, experience 40, trade_reference 50"}}, headers=W)
    assert r.status_code == 200 and r.json["answers"] == {q["id"]: "remove all", m["id"]: "company 10, experience 40, trade_reference 50"}

    job = _run(client, client.post(f"/api/exercises/{ex}/generate/runs/1/apply", headers=W))
    second = client.get(f"/api/exercises/{ex}/generate/runs/{job.result['run']}").json
    assert second["number"] == 2 and second["settings"]["review_of"] == 1
    items = _items(second)
    for i in (q["id"], m["id"]):
        assert items[i]["answer"] and items[i]["status"] and not items[i]["status"].startswith(("NOT", "PROBLEM")), items[i]
    assert second["result"]["review_problems"] == []
    assert "Do you hold a valid service licence? Yes/No" not in _sheet(client, ex, second, "AAA-ET-2026-004")
    assert any(x.startswith("review: ") for x in job.lines)

    # the QA workbook of run 2 says the same, as the CLI's --review would read it
    qa = client.get(f"/api/exercises/{ex}/files/{second['result']['qa']}").data
    ws = openpyxl.load_workbook(io.BytesIO(qa))["Decisions"]
    head = [c.value for c in ws[1]]
    row = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
    assert row[m["id"]][head.index("Your answer")] == "company 10, experience 40, trade_reference 50"


def test_runs_are_kept_and_listed(client, ex):
    _run(client, client.post(f"/api/exercises/{ex}/generate", json={"use_model": False, "only": [1]}, headers=W))
    _run(client, client.post(f"/api/exercises/{ex}/generate", json={"use_model": False}, headers=W))
    runs = client.get(f"/api/exercises/{ex}/generate").json["runs"]
    assert [(r["number"], r["status"], r["written"]) for r in runs] == [(2, "done", 3), (1, "done", 1)]
    assert runs[1]["settings"]["only"] == [1]
    folder = client.application.config["WORKSPACES"].folder(ex)
    assert sorted(os.listdir(os.path.join(folder, "runs", "1"))) == [
        "AAA-ET-2026.xlsx", "AAA-ET-2026_learned_review.xlsx", "AAA-ET-2026_qa.xlsx", "proposals.json",
        "result.json", "run.json", "settings.json"]


def test_a_sheet_not_written_says_why(client, ex):
    client.patch(f"/api/exercises/{ex}/categories/4", json={"gate_question": True}, headers=W)   # no individuals
    job = _run(client, client.post(f"/api/exercises/{ex}/generate", json={"use_model": False}, headers=W))
    run = client.get(f"/api/exercises/{ex}/generate/runs/{job.result['run']}").json
    assert [f["code"] for f in run["result"]["failed"]] == ["AAA-ET-2026-004"] and run["not_written"] == 1
    assert "Gate Question is TRUE" in run["result"]["failed"][0]["reason"]


def test_wrong_requests_say_what_to_do(client, ex, home):
    base = f"/api/exercises/{ex}/generate"
    assert client.get(f"{base}/runs/1").status_code == 404
    assert client.post(f"{base}/runs/1/apply", headers=W).status_code == 409
    _run(client, client.post(base, json={"use_model": False}, headers=W))
    r = client.patch(f"{base}/runs/1/answers", json={"answers": {"Q-nope": "remove all"}}, headers=W)
    assert r.status_code == 400 and "Q-nope" in r.json["detail"]
    assert client.post(base, json={"use_model": False}).status_code == 403                       # no header
    other = client.post("/api/exercises", json={"name": "Empty", "client": "AAA", "prefix": "X-"}, headers=W).json["id"]
    r = client.post(f"/api/exercises/{other}/generate", json={}, headers=W)
    assert r.status_code == 409 and "categories table" in r.json["detail"]
    os.remove(flows.current().bank)
    r = client.post(base, json={}, headers=W)
    assert r.status_code == 409 and "bank is empty" in r.json["detail"]


def test_an_answer_that_cannot_apply_says_why(client, ex):
    _run(client, client.post(f"/api/exercises/{ex}/generate", json={"use_model": False}, headers=W))
    first = client.get(f"/api/exercises/{ex}/generate/runs/1").json
    q = next(i for i in first["result"]["items"] if i["id"].startswith("Q-"))
    m = next(i for i in first["result"]["items"] if i["id"].startswith("M-") and i["codes"] == ["AAA-ET-2026-004"])
    client.patch(f"/api/exercises/{ex}/generate/runs/1/answers",           # marks for a section just removed
                 json={"answers": {q["id"]: "remove all", m["id"]: "technical 30, experience 10"}}, headers=W)
    job = _run(client, client.post(f"/api/exercises/{ex}/generate/runs/1/apply", headers=W))
    second = client.get(f"/api/exercises/{ex}/generate/runs/{job.result['run']}").json
    status = _items(second)[m["id"]]["status"]
    assert status.startswith("NOT APPLIED on 4: ") and "section 'technical' is not in this sheet" in status


def test_a_run_keeps_the_brief_it_read(client, ex):
    client.put(f"/api/exercises/{ex}/brief", json={"text": "Ask for site engineers."}, headers=W)
    job = _run(client, client.post(f"/api/exercises/{ex}/generate", json={"use_model": False, "only": [1]}, headers=W))
    run = client.get(f"/api/exercises/{ex}/generate/runs/{job.result['run']}").json
    assert run["settings"]["brief"] == "Ask for site engineers."
    assert any("client's brief not applied: no model" in x for x in job.lines)
