"""Spec 06 S4: the categories table, edited in place, with its problems shown row by row."""

import io
import os

import pytest

from tendersure import flows
from test_refine import _book, _upload

W = {"X-TenderSure": "1"}
OFFICE = {"number": 1, "description": "Supply of Office Stationery and Toners", "group": "GENERAL"}
CONSULT = {"number": 2, "description": "Provision of Consultancy Services", "group": "CONSULTANCY",
           "gate_question": True, "applies_to": ["Sole Proprietor", "Share Company"]}      # no Individual Consultant


@pytest.fixture
def ex(client, home):
    r = client.post("/api/exercises", json={"name": "AAA 2026", "client": "AAA", "prefix": "AAA-ET-2026-"}, headers=W)
    return r.json["id"]


def _put(client, ex, rows):
    r = client.put(f"/api/exercises/{ex}/categories", json={"categories": rows}, headers=W)
    assert r.status_code == 200, r.json
    return r.json


def test_a_gate_mismatch_is_shown_on_its_row_and_cleared_by_the_edit_that_fixes_it(client, ex):
    t = _put(client, ex, [OFFICE, CONSULT])
    assert t["errors"] == 1
    assert [(p["number"], p["field"], p["severity"]) for p in t["problems"]] == [(2, "gate_question", "error")]
    assert "Gate Question is TRUE but Applies To (Sole Proprietor, Share Company) lacks Individual Consultant" \
        in t["problems"][0]["message"]
    r = client.patch(f"/api/exercises/{ex}/categories/2", json={"applies_to": ["share company", "individual consultant"]},
                     headers=W)
    assert r.status_code == 200 and r.json["problems"] == [] and r.json["errors"] == 0
    assert r.json["categories"][1]["applies_to"] == ["Share Company", "Individual Consultant"]   # canonical names
    assert r.json["categories"][1]["client"] == "AAA" and r.json["categories"][1]["kind"]


def test_the_exported_table_builds_in_generate(client, ex, corpus, tmp_path):
    _put(client, ex, [OFFICE, {**CONSULT, "applies_to": ["Individual Consultant"]}])
    r = client.get(f"/api/exercises/{ex}/categories/categories.csv")
    assert r.status_code == 200
    (tmp_path / "cats.csv").write_bytes(r.data)
    out = flows.generate(flows.GenerateSettings(str(tmp_path / "cats.csv"), "AAA", "AAA-ET-2026-",
                                                str(tmp_path / "o.xlsx"), source=corpus, model=flows.Model(off=True)))
    assert out.written == ["AAA-ET-2026-001", "AAA-ET-2026-002"] and out.failed == []


def test_other_problems_rows_can_have(client, ex):
    t = _put(client, ex, [OFFICE, {**OFFICE, "description": " "}, {"number": 3, "description": "Supply of Fuel",
                                                                    "applies_to": ["Cooperative"]}])
    got = sorted((p["number"], p["field"], p["severity"]) for p in t["problems"])
    assert got == [(1, "description", "error"), (1, "number", "error"), (1, "number", "error"),
                   (3, "applies_to", "warning")]
    assert t["errors"] == 3 and len(t["categories"]) == 3            # saved anyway: fixed over several edits


def test_the_table_comes_from_the_category_list_and_the_old_one_is_kept(client, ex):
    _put(client, ex, [OFFICE])
    f = _upload(client, ex, "Red Cross Draft.xlsx", _book())
    client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": ["Draft list"]}, headers=W)
    rows = client.get(f"/api/exercises/{ex}/refine/list").json["rows"]
    t = client.post(f"/api/exercises/{ex}/categories/import", json={"source": "list"}, headers=W).json
    assert [c["description"] for c in t["categories"]] == [r["description"] for r in rows]
    history = os.path.join(client.application.config["WORKSPACES"].folder(ex), "history")
    assert len([n for n in os.listdir(history) if n.startswith("categories ")]) == 1


def test_a_table_can_be_imported_from_a_csv(client, ex, tmp_path):
    flows.save_categories([flows.Category(7, "Supply of Fuel", "GOODS")], str(tmp_path / "t.csv"))
    f = _upload(client, ex, "table.csv", (tmp_path / "t.csv").read_bytes())
    t = client.post(f"/api/exercises/{ex}/categories/import", json={"source": f}, headers=W).json
    assert [(c["number"], c["description"], c["client"]) for c in t["categories"]] == [(7, "Supply of Fuel", "AAA")]
    bad = _upload(client, ex, "not a table.csv", b"a,b\n1,2\n")
    r = client.post(f"/api/exercises/{ex}/categories/import", json={"source": bad}, headers=W)
    assert r.status_code == 422 and "not a categories table" in r.json["detail"]
    xl = _upload(client, ex, "book.xlsx", b"PK")
    r = client.post(f"/api/exercises/{ex}/categories/import", json={"source": xl}, headers=W)
    assert r.status_code == 422 and "not a category list" in r.json["detail"]
    doc = _upload(client, ex, "notes.pdf", b"%PDF-1.4")
    assert client.post(f"/api/exercises/{ex}/categories/import", json={"source": doc}, headers=W).status_code == 400
    assert client.post(f"/api/exercises/{ex}/categories/import", json={"source": "list"},
                       headers=W).status_code == 409                # no draft read, so no list, in this exercise


def test_an_empty_table_and_wrong_rows(client, ex):
    t = client.get(f"/api/exercises/{ex}/categories").json
    assert (t["categories"], t["problems"], t["updated"]) == ([], [], None)
    assert client.get(f"/api/exercises/{ex}/categories/categories.csv").status_code == 404
    _put(client, ex, [OFFICE])
    assert client.patch(f"/api/exercises/{ex}/categories/9", json={"group": "X"}, headers=W).status_code == 404
    r = client.patch(f"/api/exercises/{ex}/categories/1", json={"group": "OFFICE"}, headers=W).json
    assert r["categories"][0]["group"] == "OFFICE" and r["categories"][0]["description"] == OFFICE["description"]
    assert client.put(f"/api/exercises/{ex}/categories", json={"categories": []}).status_code == 403   # no header


def test_the_client_s_approved_list_can_be_imported(client, ex, tmp_path):
    """Spec 17 D: the client's list (.xlsx in the draft's layout) becomes the table."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B3"], ws["C3"] = "No", "Category Description"
    for i, (n, text) in enumerate([(None, "GENERAL"), (1, "Supply of office stationery"), (2, "Provision of Fumigation")], 4):
        ws.cell(i, 2, n)
        ws.cell(i, 3, text)
    wb.save(tmp_path / "final.xlsx")
    f = _upload(client, ex, "final.xlsx", (tmp_path / "final.xlsx").read_bytes())
    t = client.post(f"/api/exercises/{ex}/categories/import", json={"source": f}, headers=W).json
    assert [(c["number"], c["description"], c["group"]) for c in t["categories"]] == [
        (1, "Supply of office stationery", "GENERAL"), (2, "Provision of Fumigation", "GENERAL")]
