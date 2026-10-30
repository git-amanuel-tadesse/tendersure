"""Spec 06 S3: refinement in the browser - draft, proposal, review, Excel both ways, apply."""

import io
import os

import openpyxl
import pytest

from tendersure import flows

W = {"X-TenderSure": "1"}
RC = [(None, "GENERAL"),
      (1, "Supply of office stationery"),
      (2, "Provision of Translation and Interpretation services"),
      (3, "Provision of travel agency services (IATA Registered Only)"),
      ("ICT", None),
      (4, "Supply, installation and maintenance of ICT equipment (Servers, Routers)"),
      (5, "Supply and Maintenance of ICT Equipment (Computers, Laptops)"),
      (6, "Provision of repairs and maintenance of fire fighting eqiupment"),
      (7, "Provision of repairs and maintenance of fire fighting eqiupment")]


def _book(rows=RC, tab="Draft list"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = tab
    wb.create_sheet("Notes")
    ws["B3"], ws["C3"] = "No", "Category Description"
    for i, (num, text) in enumerate(rows, 4):
        ws.cell(i, 2, num)
        ws.cell(i, 3, text)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.fixture
def ex(client, home):
    r = client.post("/api/exercises", json={"name": "RC 2026", "client": "AAA", "prefix": "AAA-ET-2026-"}, headers=W)
    return r.json["id"]


def _upload(client, eid, name, data):
    r = client.post(f"/api/exercises/{eid}/files", data={"file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data", headers=W)
    assert r.status_code == 201, r.json
    return r.json["path"]


def _job(client, r):
    assert r.status_code == 202, r.json
    job = client.application.config["JOBS"].wait(r.json["id"])
    assert job.status == "done", job.error
    return job


def _proposed(client, eid, data=None):
    f = _upload(client, eid, "Red Cross Draft.xlsx", data or _book())
    r = client.post(f"/api/exercises/{eid}/refine/draft", json={"file": f, "sheet": "Draft list"}, headers=W)
    assert r.status_code == 200, r.json
    _job(client, client.post(f"/api/exercises/{eid}/refine/propose", json={"use_model": False}, headers=W))
    return client.get(f"/api/exercises/{eid}/refine").json


def _index(state, draft_id):
    return next(i for i, ln in enumerate(state["revised"]["lines"]) if ln["from_draft"] == [draft_id])


def test_the_browser_loop_gives_the_clis_final_list(client, ex, tmp_path):
    state = _proposed(client, ex)
    assert state["draft"]["tabs"] == ["Draft list", "Notes"] and len(state["draft"]["drafts"]) == 7
    edits = [{"index": _index(state, "1"), "decision": "edit", "comment": "Supply of Stationery and Toners"},
             {"index": _index(state, "2"), "decision": "remove"}]
    r = client.patch(f"/api/exercises/{ex}/refine/lines", json={"changes": edits}, headers=W)
    assert r.status_code == 200 and r.json["revised"]["lines"][edits[1]["index"]]["decision"] == "remove"
    job = _job(client, client.post(f"/api/exercises/{ex}/refine/apply", json={"use_model": False}, headers=W))
    assert any("7 draft rows -> 5 final categories" in x for x in job.lines)
    browser = [(c["number"], c["description"], c["applies_to"]) for c in job.result["categories"]]

    # the CLI's way: the revised workbook, answered in Excel, applied
    (tmp_path / "d.xlsx").write_bytes(_book())
    revised = tmp_path / "rev.xlsx"
    flows.propose_file(str(tmp_path / "d.xlsx"), "AAA", "Draft list", out=str(revised), model=flows.Model(off=True))
    wb = openpyxl.load_workbook(revised)
    ws = wb["Revised"]
    col = {c.value: c.column for c in ws[1]}
    rows = {str(ws.cell(r, col["From draft #"]).value): r for r in range(2, ws.max_row + 1) if ws.cell(r, col["Status"]).value}
    ws.cell(rows["1"], col["Decision"], "edit")
    ws.cell(rows["1"], col["Client comment"], "Supply of Stationery and Toners")
    ws.cell(rows["2"], col["Decision"], "remove")
    wb.save(revised)
    cli = flows.apply_file(str(revised), "AAA", model=flows.Model(off=True))
    assert browser == [(c.number, c.description, c.applies_to) for c in cli.categories]
    table = flows.load_categories(os.path.join(client.application.config["WORKSPACES"].folder(ex), "categories.csv"))
    assert [c.description for c in table] == [b[1] for b in browser]          # the table generation reads


def test_the_revised_workbook_goes_out_and_answers_come_back(client, ex):
    state = _proposed(client, ex)
    r = client.get(f"/api/exercises/{ex}/refine/revised.xlsx")
    assert r.status_code == 200 and "Red Cross Draft - Revised.xlsx" in r.headers["Content-Disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(r.data))                            # the client answers it in Excel
    ws = wb["Revised"]
    col = {c.value: c.column for c in ws[1]}
    row = next(r for r in range(2, ws.max_row + 1) if str(ws.cell(r, col["From draft #"]).value) == "3")
    ws.cell(row, col["Decision"], "remove")
    ws.cell(row, col["Client comment"], "not needed this year")
    out = io.BytesIO()
    wb.save(out)
    f = _upload(client, ex, "Red Cross Draft - Revised answered.xlsx", out.getvalue())

    preview = client.post(f"/api/exercises/{ex}/refine/import", json={"file": f}, headers=W).json
    i = _index(state, "3")
    assert preview["applied"] is False and preview["unmatched"] == []
    assert {(c["index"], c["field"], c["after"]) for c in preview["changes"]} == {
        (i, "decision", "remove"), (i, "comment", "not needed this year")}
    assert client.get(f"/api/exercises/{ex}/refine").json["revised"]["lines"][i]["decision"] == ""   # not yet

    done = client.post(f"/api/exercises/{ex}/refine/import", json={"file": f, "confirm": True}, headers=W).json
    assert done["applied"] is True
    assert client.get(f"/api/exercises/{ex}/refine").json["revised"]["lines"][i]["decision"] == "remove"


def test_answers_to_another_draft_are_refused(client, ex, tmp_path):
    _proposed(client, ex)
    (tmp_path / "other.xlsx").write_bytes(_book([(1, "Supply of fuel")]))
    flows.propose_file(str(tmp_path / "other.xlsx"), "AAA", out=str(tmp_path / "other_rev.xlsx"), model=flows.Model(off=True))
    f = _upload(client, ex, "other revised.xlsx", (tmp_path / "other_rev.xlsx").read_bytes())
    r = client.post(f"/api/exercises/{ex}/refine/import", json={"file": f, "confirm": True}, headers=W)
    assert r.status_code == 409 and "another draft" in r.json["detail"]


def test_a_new_draft_starts_again_and_keeps_the_old_proposal(client, ex):
    _proposed(client, ex)
    f = _upload(client, ex, "second draft.xlsx", _book(RC[:3]))
    state = client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f}, headers=W).json
    assert len(state["draft"]["drafts"]) == 2 and state["revised"] is None
    history = os.path.join(client.application.config["WORKSPACES"].folder(ex), "refine", "history")
    assert [sorted(os.listdir(os.path.join(history, d))) for d in os.listdir(history)] == [["draft.json", "revised.json"]]


def test_wrong_requests_say_what_to_do(client, ex):
    base = f"/api/exercises/{ex}/refine"
    assert client.post(f"{base}/propose", json={}, headers=W).status_code == 409             # no draft yet
    assert client.post(f"{base}/apply", json={}, headers=W).status_code == 409               # no proposal yet
    assert client.get(f"{base}/final.xlsx").status_code == 409
    assert client.post(f"{base}/draft", json={"file": "uploads/none.xlsx"}, headers=W).status_code == 404
    f = _upload(client, ex, "draft.xlsx", _book())
    r = client.post(f"{base}/draft", json={"file": f, "sheet": "Nope"}, headers=W)
    assert r.status_code == 422 and "has no tab 'Nope'" in r.json["detail"]
    r = client.post(f"{base}/draft", json={"file": f, "sheet": "Notes"}, headers=W)          # a tab with no list
    assert r.status_code == 422
    state = _proposed(client, ex)
    n = len(state["revised"]["lines"])
    r = client.patch(f"{base}/lines", json={"changes": [{"index": 0, "decision": "maybe"}]}, headers=W)
    assert r.status_code == 400 and "is not one of accept, reject, remove, edit, split, merge" in r.json["detail"]
    r = client.patch(f"{base}/lines", json={"changes": [{"index": n, "decision": "remove"}]}, headers=W)
    assert r.status_code == 400
    assert client.patch(f"{base}/lines", json={"changes": []}).status_code == 403          # no header
    assert client.post(f"{base}/propose", json={"target": [60, 50]}, headers=W).status_code == 400
    assert client.get("/api/exercises/nope/refine").status_code == 404



def test_propose_reads_the_draft_again(client, ex):
    """A draft chosen before the reader learned a column is read again when proposing."""
    import json
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B3"], ws["C3"], ws["D3"] = "No", "Category Description", "HR"
    ws["B4"], ws["C4"], ws["D4"] = 1, "Provision of minor construction works", "Foreman, Site engineer"
    buf = io.BytesIO()
    wb.save(buf)
    f = _upload(client, ex, "d.xlsx", buf.getvalue())
    client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f}, headers=W)
    path = os.path.join(client.application.config["WORKSPACES"].folder(ex), "refine", "draft.json")
    with open(path) as fh:
        stale = json.load(fh)
    stale["drafts"] = [{"id": "1", "description": "Provision of minor construction works", "group": None}]
    with open(path, "w") as fh:
        json.dump(stale, fh)                                   # as an older reader left it
    _job(client, client.post(f"/api/exercises/{ex}/refine/propose", json={"use_model": False}, headers=W))
    state = client.get(f"/api/exercises/{ex}/refine").json
    assert state["revised"]["lines"][0]["hr"] == ["Foreman", "Site engineer"]
    assert state["draft"]["drafts"][0]["hr"] == ["Foreman", "Site engineer"]


def test_a_split_and_a_merge_answered_in_the_browser(client, ex):
    state = _proposed(client, ex)
    lines = state["revised"]["lines"]
    a = _index(state, "4")                      # the two ICT equipment rows overlap
    b = _index(state, "5")
    assert str(b + 1) in lines[a]["suggested_merge"]
    r = client.patch(f"/api/exercises/{ex}/refine/lines", json={"changes": [
        {"index": a, "decision": "merge", "merge_with": [str(b + 1)], "comment": "Supply of ICT Equipment"},
        {"index": _index(state, "2"), "decision": "split",
         "split_into": ["Provision of Translation Services", "Provision of Interpretation Services"]}]}, headers=W)
    assert r.status_code == 200, r.json
    _job(client, client.post(f"/api/exercises/{ex}/refine/apply", json={}, headers=W))
    final = [c["description"] for c in client.get(f"/api/exercises/{ex}/refine").json["final"]["categories"]]
    assert "Supply of ICT Equipment" in final
    assert {"Provision of Translation Services", "Provision of Interpretation Services"} <= set(final)


def test_re_applying_keeps_the_team_s_table_edits(client, ex):
    """PSI: a second apply dropped a gate and the technical items set on the categories page."""
    _proposed(client, ex)
    _job(client, client.post(f"/api/exercises/{ex}/refine/apply", json={}, headers=W))
    table = client.get(f"/api/exercises/{ex}/categories").json["categories"]
    first = table[0]
    r = client.patch(f"/api/exercises/{ex}/categories/{first['number']}", json={"technical": ["MAF/DAF"],
                     "gate_question": True, "applies_to": ["Share Company", "Individual Consultant"]}, headers=W)
    assert r.status_code == 200
    job = _job(client, client.post(f"/api/exercises/{ex}/refine/apply", json={}, headers=W))
    again = client.get(f"/api/exercises/{ex}/categories").json["categories"][0]
    assert (again["technical"], again["gate_question"], again["applies_to"]) == \
        (["MAF/DAF"], True, ["Share Company", "Individual Consultant"])
    assert any("kept as the team set it" in x for x in job.lines)
    folder = client.application.config["WORKSPACES"].folder(ex)
    assert os.listdir(os.path.join(folder, "history"))                      # the table before is kept
