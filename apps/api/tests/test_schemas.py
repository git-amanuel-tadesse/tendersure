"""Spec 06 M2: the API's shapes mirror the flows' types exactly, both ways, through JSON."""

import dataclasses

import openpyxl
import pytest

from tendersure import flows
from tendersure_api import schemas as S

OFF = flows.Model(off=True)

SAMPLES = [
    flows.Draft("r12", "Supply of office stationery", "GENERAL"),
    flows.Line("Supply of Office Stationery", "Renamed", ["1", "7"], "GENERAL", "typo fixed", "Toners too?",
               ["Paper", "Toners / Cartridges"], ["Trade License"], ["Share Company", "Individual Consultant"],
               "G0001", "edit", "Supply of Stationery", ["ISO 9001"], "Office Supplies", ["Paper", "Toners"],
               ["Stationery | https://example.org/s"]),
    flows.Category(19, "Provision of Consultancy Services", "CONSULTANCY", ["Training", "Research"], "ERCS",
                   "2026-09-ext", "cover methodology", ["CV"], True, ["Individual Consultant"]),
    flows.Item("M-ERCS-ET-2026-019-abc", "marks", "Marks for 019", ["ERCS-ET-2026-019"], "technical 30", "why",
               "technical 25, experience 15", ["k1"], "applied", {"client": "ERCS", "rule": "x"}, [{"n": 1}]),
]


@pytest.mark.parametrize("obj", SAMPLES, ids=lambda o: type(o).__name__)
def test_every_type_maps_both_ways_through_json_without_loss(obj):
    model = getattr(S, type(obj).__name__)
    again = model.model_validate_json(model.of(obj).model_dump_json()).core()
    assert again == obj and type(again) is type(obj)


@pytest.mark.parametrize("name", ["Draft", "Line", "Category", "Item"])
def test_the_api_lists_exactly_the_core_fields(name):
    model = getattr(S, name)
    assert set(model.model_fields) == {f.name for f in dataclasses.fields(model.CORE)}   # drift fails here


def test_derived_values_are_shown_read_only():
    line = S.Line.of(SAMPLES[1]).model_dump()
    assert line["gate_question"] is True                     # Individual Consultant among those it applies to
    assert S.Category.of(SAMPLES[2]).model_dump()["kind"] == SAMPLES[2].kind
    assert S.Line.model_validate(line).core() == SAMPLES[1]  # sent back, they are ignored


def _draft(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B3"], ws["C3"] = "No", "Category Description"
    for i, (n, t) in enumerate([(None, "GENERAL"), (1, "Supply of office stationery"),
                                (2, "Provision of travel agency services"), (3, "Provision of travel agency services")], 4):
        ws.cell(i, 2, n)
        ws.cell(i, 3, t)
    wb.save(path)
    return str(path)


def test_refinement_results_map(tmp_path):
    r = flows.propose_file(_draft(tmp_path / "d.xlsx"), "AAA", target=(2, 3), out=str(tmp_path / "rev.xlsx"),
                           bank=str(tmp_path / "none"), research=str(tmp_path / "r.csv"), model=OFF)
    out = S.ProposeResult.model_validate_json(S.ProposeResult.of(r).model_dump_json())
    assert [ln.core() for ln in out.lines] == r.lines and out.target == [2, 3] and out.statuses == r.statuses
    r.lines[0].decision = "remove"
    done = S.ApplyResult.of(flows.apply(r.drafts, r.lines, "AAA", model=OFF))
    assert [c.core() for c in done.categories] and done.excluded[0].decision == "remove"


def test_generation_results_map(corpus, tmp_path):
    from tendersure import categories as cat
    cat.save([flows.Category(1, "Supply of Office Stationery and Toners", "GENERAL", client="AAA"),
              flows.Category(2, "Supply of Cleaning Materials", "GENERAL", client="AAA")], str(tmp_path / "c.csv"))
    r = flows.generate(flows.GenerateSettings(str(tmp_path / "c.csv"), "AAA", "AAA-ET-2026-", str(tmp_path / "o.xlsx"),
                                              source=corpus, model=OFF))
    out = S.GenerateResult.model_validate_json(S.GenerateResult.of(r).model_dump_json())
    assert out.written == r.written and [i.core() for i in out.items] == r.items
    assert sum(out.open.values()) == len(r.open_items)
