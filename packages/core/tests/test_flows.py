"""The flows (spec 06 W1): the steps every app calls, reporting through the
logger and returning results, never printing or exiting."""

import logging

import openpyxl
import pytest

from tendersure import categories as cat
from tendersure import flows
from test_pipeline import RC, _draft_book

OFF = flows.Model(off=True)


def _settings(corpus, d, out="o.xlsx", **kw):
    """`d` is a folder outside the corpus (a build reads every workbook under the corpus, so its own
    output there would become history for the next build)."""
    return flows.GenerateSettings(
        str(d / "cats.csv"), "AAA", "AAA-ET-2026-", str(d / out), source=str(corpus),
        audience=str(d / "aud.csv"), glossary=str(d / "g.csv"), yes_no=str(d / "yn.csv"),
        rulings=str(d / "rulings.csv"), research=str(d / "research.csv"), model=OFF, **kw)


def _cells(path):
    wb = openpyxl.load_workbook(path)
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb}


def test_refine_flows_report_through_the_logger_and_return_the_lines(tmp_path, caplog, capsys):
    draft = _draft_book(tmp_path / "rc.xlsx", RC)
    with caplog.at_level(logging.INFO, logger="tendersure"):
        r = flows.propose_file(draft, "AAA", out=str(tmp_path / "revised.xlsx"), bank=str(tmp_path / "none"),
                               research=str(tmp_path / "research.csv"), model=OFF)
    assert capsys.readouterr().out == ""                            # a flow never prints
    assert any(m.startswith("refine: 7 draft rows -> ") for m in caplog.messages)
    assert r.out == str(tmp_path / "revised.xlsx") and len(r.drafts) == 7
    assert sum(r.statuses.values()) == len(r.lines) and r.proposed == len(r.lines) - r.statuses.get("Repeated", 0)

    # the review held in memory, as the web app holds it: no workbook needed
    first = next(ln for ln in r.lines if ln.status != "Repeated")
    first.decision = "remove"
    done = flows.apply(r.drafts, r.lines, "AAA", model=OFF)
    assert first in done.excluded and first not in done.final
    assert [c.description for c in done.categories] == [ln.description for ln in done.final]
    assert [c.number for c in done.categories] == list(range(1, len(done.final) + 1))


def test_generate_takes_the_review_in_memory_exactly_as_from_the_qa_file(corpus, tmp_path_factory):
    from tendersure.cli import main
    from tendersure.review import read_proposals
    out = tmp_path_factory.mktemp("out")
    cat.save([cat.Category(1, "Supply of Office Stationery and Toners", "GENERAL", client="AAA"),
              cat.Category(2, "Supply of Cleaning Materials", "GENERAL", client="AAA")], str(out / "cats.csv"))
    first = flows.generate(_settings(corpus, out))
    assert first.written == ["AAA-ET-2026-001", "AAA-ET-2026-002"] and first.failed == []
    m = next(i for i in first.items if i.id.startswith("M-"))

    # answer the M line in the QA workbook, as the team does in Excel
    wb = openpyxl.load_workbook(first.qa)
    ws = wb["Decisions"]
    head = [c.value for c in ws[1]]
    row = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value == m.id)
    ws.cell(row, head.index("Your answer") + 1, "technical 30, experience 10")
    wb.save(first.qa)

    main(["generate", str(corpus), "--categories", str(out / "cats.csv"), "--client", "AAA",
          "--prefix", "AAA-ET-2026-", "--out", str(out / "from_file.xlsx"), "--no-model",
          "--audience", str(out / "aud.csv"), "--glossary", str(out / "g.csv"),
          "--yes-no", str(out / "yn.csv"), "--rulings", str(out / "rulings.csv"),
          "--research", str(out / "research.csv"), "--review", first.qa])
    in_memory = flows.generate(_settings(corpus, out, "in_memory.xlsx",
                                         proposals=read_proposals(first.qa), answers={m.id: "technical 30, experience 10"}))
    assert _cells(out / "from_file.xlsx") == _cells(out / "in_memory.xlsx")
    applied = next(i for i in in_memory.items if i.id == m.id)
    assert applied.answer == "technical 30, experience 10" and applied.status and "NOT" not in applied.status
    assert _cells(out / "in_memory.xlsx") != _cells(first.files[0])      # the answer changed the sheet


def test_a_flow_that_cannot_go_on_raises_instead_of_exiting(corpus, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    cat.save([cat.Category(1, "Supply of Office Stationery and Toners", "GENERAL", client="AAA")],
             str(out / "cats.csv"))
    with pytest.raises(flows.FlowError, match="nothing to write"):
        flows.generate(_settings(corpus, out, only=[99]))
    with pytest.raises(flows.FlowError, match="no bank at"):
        flows.load_source(None, str(out / "missing.sqlite"))
