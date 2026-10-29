"""Spec 06 S1, S2: the settings (never a key) and the bank, ingested from the browser."""

import io
import os

import openpyxl

from tendersure import flows

W = {"X-TenderSure": "1"}


def _ingest(client, files):
    r = client.post("/api/bank/ingest", data={"files": [(io.BytesIO(b), n) for n, b in files]},
                    content_type="multipart/form-data", headers=W)
    assert r.status_code == 202, r.json
    return client.application.config["JOBS"].wait(r.json["id"])


def _corpus_files(corpus):
    return [(f"history/{n}", open(os.path.join(corpus, n), "rb").read()) for n in sorted(os.listdir(corpus))]


def _content(bank):
    from tendersure.store import BankStore
    return sorted((s.name, s.code, s.client, s.description, tuple((r.section, r.criteria, r.marks) for r in s.rows))
                  for s in BankStore(bank).load_sheets())


def test_ingesting_from_the_browser_gives_the_same_bank_as_the_cli(client, home, corpus, tmp_path):
    job = _ingest(client, _corpus_files(corpus))
    assert job.status == "done", job.error
    assert job.result["added"] == 2 and job.result["sheets"] == 4
    assert any(x.startswith("bank ") and "2 workbook(s) added" in x for x in job.lines)
    flows.ingest(corpus, str(tmp_path / "cli.sqlite"), str(tmp_path / "cli-reports"))        # the CLI's step
    assert _content(flows.current().bank) == _content(str(tmp_path / "cli.sqlite"))
    staging = os.path.join(client.application.config["WORKSPACES"].root, ".staging")
    assert os.path.isdir(staging) and os.listdir(staging) == []            # the uploaded copies are gone


def test_the_same_upload_again_replaces_instead_of_adding(client, home, corpus):
    files = _corpus_files(corpus)
    _ingest(client, files)
    assert _ingest(client, files).result["unchanged"] == 2                  # same bytes: nothing to do
    wb = openpyxl.load_workbook(io.BytesIO(files[0][1]))
    wb.active.cell(2, 2, "AAA-ET-2026-001: Supply of Office Stationery, Toners and Paper")
    changed = io.BytesIO()
    wb.save(changed)
    r = _ingest(client, [(files[0][0], changed.getvalue())]).result
    assert (r["replaced"], r["added"]) == (1, 1)
    assert client.get("/api/bank").json["workbooks"] == 2                   # still two workbooks, not three


def test_the_bank_status_and_its_reports(client, home, corpus):
    assert client.get("/api/bank").json["exists"] is False
    assert client.get("/api/bank/reports/bank.xlsx").status_code == 404
    _ingest(client, _corpus_files(corpus))
    b = client.get("/api/bank").json
    assert (b["exists"], b["workbooks"], b["sheets"], b["clients"]) == (True, 2, 4, ["AAA", "BBB"]) and b["last_ingest"]
    r = client.get("/api/bank/reports/validation_report.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    for name in ("../.env", "glossary.csv", "%2e%2e%2f.env"):
        assert client.get(f"/api/bank/reports/{name}").status_code == 404


def test_unsafe_or_wrong_uploads_are_refused(client, home):
    for name in ("../x.xlsx", "a/../b.xlsx", ".git/x.xlsx", "history/x.csv", "/etc/x.xlsx"):
        r = client.post("/api/bank/ingest", data={"files": [(io.BytesIO(b"x"), name)]},
                        content_type="multipart/form-data", headers=W)
        assert r.status_code == 400, name
    r = client.post("/api/bank/ingest", data={"files": [(io.BytesIO(b"x"), "~$lock.xlsx")]},
                    content_type="multipart/form-data", headers=W)
    assert r.status_code == 400 and "no .xlsx" in r.json["detail"]           # Excel's lock files are skipped


def test_the_settings_say_which_model_runs_and_never_the_key(client, home, monkeypatch):
    monkeypatch.setenv("TENDERSURE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key-123")
    monkeypatch.setenv("TENDERSURE_MODEL", "gemini-4-argon")
    monkeypatch.setenv("GEMINI_PAID_TIER", "no")
    r = client.get("/api/settings")
    assert r.json["model"] == {"runs": False, "why_not": "GEMINI_PAID_TIER is not 'yes' (unpaid keys may expose prompts)",
                               "provider": "gemini", "model": "gemini-4-argon"}
    monkeypatch.setenv("GEMINI_PAID_TIER", "yes")
    r = client.get("/api/settings")
    assert r.json["model"]["runs"] is True and r.json["home"] == str(home)
    assert "secret-key-123" not in r.get_data(as_text=True)
