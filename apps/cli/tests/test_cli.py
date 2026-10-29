"""The CLI is arguments in, flows out: each command reaches its flow, the
flow's lines are printed, and failures become exit codes."""

import shutil
import subprocess
import sys

import openpyxl
import pytest

from tendersure import categories as cat
from tendersure_cli import main

DRAFT = [(None, "GENERAL"), (1, "Supply of office stationery"), (2, "Provision of travel agency services"),
         (3, "Provision of travel agency services")]


def _draft(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B3"], ws["C3"] = "No", "Category Description"
    for i, (num, text) in enumerate(DRAFT, 4):
        ws.cell(i, 2, num)
        ws.cell(i, 3, text)
    wb.save(path)
    return str(path)


def _generate(corpus, out, *extra):
    cat.save([cat.Category(1, "Supply of Office Stationery and Toners", "GENERAL", client="AAA"),
              cat.Category(2, "Supply of Cleaning Materials", "GENERAL", client="AAA")], str(out / "cats.csv"))
    main(["generate", corpus, "--categories", str(out / "cats.csv"), "--client", "AAA", "--prefix", "AAA-ET-2026-",
          "--out", str(out / "gen.xlsx"), "--no-model", *extra])
    return out / "gen.xlsx"


def test_the_flows_lines_are_printed_and_the_review_loop_runs(tmp_path, capsys):
    revised, final, table = tmp_path / "revised.xlsx", tmp_path / "final.xlsx", tmp_path / "cats.csv"
    main(["refine", _draft(tmp_path / "draft.xlsx"), "--client", "AAA", "--no-model", "--out", str(revised),
          "--bank", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert "refine: without a model (--no-model given)" in out and f"-> {revised}" in out
    main(["refine", "--client", "AAA", "--apply", str(revised), "--out", str(final), "--categories-out", str(table),
          "--no-model"])
    out = capsys.readouterr().out
    assert "3 draft rows -> 2 final categories" in out and f"categories table -> {table}" in out
    assert [c.number for c in cat.load(str(table))] == [1, 2]


def test_check_exits_1_on_errors_and_0_on_a_clean_workbook(corpus, tmp_path, capsys):
    gen = _generate(corpus, tmp_path)
    main(["check", str(gen), "--client", "AAA", "--no-model", "--out", str(tmp_path / "r1.xlsx")])   # no exit
    edited = tmp_path / "edited.xlsx"
    shutil.copy(gen, edited)
    wb = openpyxl.load_workbook(edited)
    ws = wb["AAA-ET-2026-001"]
    row = next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 4).value == "Attach evidence e.g. PO or award letter")
    ws.cell(row, 5, 45)                                                        # the sheet no longer adds up
    wb.save(edited)
    with pytest.raises(SystemExit) as e:
        main(["check", str(edited), "--client", "AAA", "--no-model", "--out", str(tmp_path / "r2.xlsx")])
    assert e.value.code == 1 and "check: " in capsys.readouterr().out


def test_review_answers_are_read_from_the_qa_file(corpus, tmp_path, capsys):
    gen = _generate(corpus, tmp_path)
    qa = tmp_path / "gen_qa.xlsx"
    wb = openpyxl.load_workbook(qa)
    ws = wb["Decisions"]
    head = [c.value for c in ws[1]]
    row = next(r for r in range(2, ws.max_row + 1) if str(ws.cell(r, 1).value).startswith("M-"))
    ws.cell(row, head.index("Your answer") + 1, "technical 30, experience 10")
    wb.save(qa)
    before = [[c.value for c in r] for r in openpyxl.load_workbook(gen)["AAA-ET-2026-002"].iter_rows()]
    capsys.readouterr()
    _generate(corpus, tmp_path, "--review", str(qa))
    after = [[c.value for c in r] for r in openpyxl.load_workbook(gen)["AAA-ET-2026-002"].iter_rows()]
    assert after != before                                                  # the answer reached the build
    assert "REVIEW PROBLEM" not in capsys.readouterr().out


def test_a_flow_that_cannot_go_on_becomes_the_exit_message(corpus, tmp_path):
    with pytest.raises(SystemExit) as e:
        _generate(corpus, tmp_path, "--only", "99")
    assert str(e.value.code).startswith("nothing to write")
    with pytest.raises(SystemExit) as e:
        main(["generate", "--categories", str(tmp_path / "none.csv"), "--client", "AAA", "--prefix", "X-",
              "--out", str(tmp_path / "o.xlsx")])
    assert str(e.value.code).startswith("categories file not found")


def test_the_tendersure_command_is_installed():
    done = subprocess.run([sys.executable, "-m", "tendersure_cli", "--help"], capture_output=True, text=True)
    assert done.returncode == 0 and "generate" in done.stdout and "usage: tendersure" in done.stdout
