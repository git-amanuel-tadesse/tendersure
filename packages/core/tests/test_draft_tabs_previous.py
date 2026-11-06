"""Spec 18: a draft over several tabs, sub-category lines, and the client's previous list.

The workbooks are synthetic, laid out as Amref's (2026-11-04): a hidden empty
first tab, a "serial" header, a heading not in capitals, numbers left inside
descriptions, dash lines under a category, and last round's results with
suppliers under each category header."""

import logging

import openpyxl
import pytest

from tendersure import flows, refine as rf
from tendersure.previous import read_previous

GOODS = [("Catagory list title, not a header", None),
         ("serial", "List of Item Catagories"),
         (1, "Supply of general stationeries and tonners"),
         (2, "Provision of event management services such as conferences"),
         (None, "ICT"),
         (3, "Supply of mobile phones, tablets, and related accessories"),
         (None, "Engineering"),
         (4, "Repair, renovation and construction of buildings- Grade 5 and above"),
         (5, "3\xa0Provision of Strategic Communications & Campaigns")]
CONSULTANCY = [("serial", "List of Item Catagories"),
               (1, "Project/Programme research evaluations"),
               (None, "\xa0Provision of Project-Based Consultancy Services"),
               (None, "–\xa0Feasibility Study and Assessment"),
               (None, "–\xa0Market Survey"),
               (2, "Provision of event management and coordination services"),
               (3, "Artistic Works, Layout Designs & Illustrations"),
               (None, "Visual graphics and illustration of training manuals for the field teams"),
               (4, "Artistic Works, Layout Designs & Illustrations")]
SUPPLIERS = ["WEBZONE TRADING PLC", "Hey Trading One Member PLC", "ACME Consult PLC"]


def _tab(ws, rows):
    for r, (a, b) in enumerate(rows, 1):
        ws.cell(r, 1, a)
        ws.cell(r, 2, b)


def _amref_like(path):
    wb = openpyxl.Workbook()
    wb.active.title = "CoverP"
    wb.active.sheet_state = "hidden"
    _tab(wb.create_sheet("Catagory for Goods and services"), GOODS)
    _tab(wb.create_sheet("Catagory for Consultancy"), CONSULTANCY)
    ws = wb.create_sheet("Prequalified Suppl. goods &serv")              # code headers, suppliers under them
    for row in [(None, "Amref Health Africa in Ethiopia"), (None, "Preliminary Evaluation"), ("serial", "Supplier"),
                (None, "G/001/2025-2026 / Supply of general stationeries (17)"), (1, SUPPLIERS[0]),
                (None, SUPPLIERS[1]),                                     # a supplier row without a number
                (None, "S/018/2025-2026 / Hire of mobile toilets (1)"), (1, SUPPLIERS[0]),
                (None, "S/024/2025-2026 / Provision of taxi services (2)"), (1, SUPPLIERS[1]),
                (None, "NCS / No category Selected (76)"), (1, "Someone Without A Category")]:
        ws.append(list(row))
    ws = wb.create_sheet("Prequalified_Consultant")                      # merged headers above the suppliers
    ws.append([None, "Amref Health Africa in Ethiopia"])
    ws.append([None, "Supplier Name", "Score", None, "Email", "Phone", "Location"])
    for name in ("Event management and coordination", "Statisticians", "Statisticians"):
        ws.append([name])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=7)
        ws.append([1, SUPPLIERS[2], 50, None, "info@acme.example", "+251900000000", "Addis Ababa"])
    wb.save(path)
    return str(path)


TABS = ["Catagory for Goods and services", "Catagory for Consultancy"]


@pytest.fixture
def book(tmp_path):
    return _amref_like(tmp_path / "amref.xlsx")


# --- A. reading the draft ---------------------------------------------------------------

def test_a_serial_header_a_hidden_tab_and_a_heading_in_lower_case(book):
    drafts = rf.read_draft(book)                     # no tab named: the hidden CoverP is skipped
    assert [d.id for d in drafts] == ["1", "2", "3", "4", "5"]
    assert [d.group for d in drafts] == [None, None, "ICT", "Engineering", "Engineering"]
    with pytest.raises(ValueError, match="hidden"):
        rf.read_draft(book, "CoverP")
    assert flows.draft_tabs(book) == TABS + ["Prequalified Suppl. goods &serv", "Prequalified_Consultant"]


def test_an_old_number_in_a_description_is_dropped_and_shown_as_renamed(book):
    drafts = rf.read_draft(book, TABS[0])
    line = next(ln for ln in rf.check(None, drafts, rf.groups_of(drafts)) if ln.from_draft == ["5"])
    assert line.description == "Provision of Strategic Communications & Campaigns"
    assert (line.status, line.reason) == (rf.RENAMED, "old number dropped; was: 3 Provision of Strategic "
                                                      "Communications & Campaigns")


@pytest.mark.parametrize("head", ["Serial", "S.No", "Sr. No", "Serial No", "No."])
def test_number_column_headers(tmp_path, head):
    wb = openpyxl.Workbook()
    wb.active.append([head, "Category Code", "Category Description"])
    wb.active.append([1, "G1", "Supply of stationery"])
    wb.save(tmp_path / "d.xlsx")
    assert [(d.id, d.description) for d in rf.read_draft(str(tmp_path / "d.xlsx"))] == [("1", "Supply of stationery")]


# --- B. several tabs, one draft -----------------------------------------------------------

def test_two_tabs_are_one_draft_checked_as_one_list(book):
    drafts = rf.read_draft(book, TABS)
    assert len({d.id for d in drafts}) == len(drafts) == 10                  # 5 + 5, one unplaced
    by = {d.id: d for d in drafts}
    assert (by["1-1"].group, by["2-1"].group) == ("Goods and Services", "Consultancy")
    assert by["1-3"].group == "ICT"
    lines = {ln.from_draft[0]: ln for ln in rf.check(None, drafts, rf.groups_of(drafts))}
    assert (lines["2-4"].status, lines["2-4"].reason) == (rf.REPEATED, "same as #2-3")
    assert "#1-2" in lines["2-2"].question                    # event management, across the tabs
    assert rf.tab_group("Category list for ICT works") == "ICT Works"


def test_the_flow_says_which_tab_is_missing(book):
    with pytest.raises(flows.FlowError, match="no tab"):
        flows.read_draft(book, ["Nope"])


# --- C. sub-category lines ------------------------------------------------------------------

def test_dash_lines_and_the_line_above_them_are_sub_categories(book):
    by = {d.id: d for d in rf.read_draft(book, TABS[1])}
    assert by["1"].sub_categories == ["Provision of Project-Based Consultancy Services",
                                      "Feasibility Study and Assessment", "Market Survey"]
    assert sorted(by) == ["1", "2", "3", "4", "r8"]           # no line from the sub-category rows
    assert by["r8"].notes == [rf.UNPLACED]                    # neither a heading nor a sub-category


def test_a_separated_line_takes_the_sub_categories_the_model_gave_it():
    drafts = [rf.Draft("1", "Project research evaluations", "C", ["Feasibility Study", "Market Survey",
                                                                  "Baseline Survey", "Midterm Evaluation"])]
    raw = {"lines": [{"from": ["1"], "status": "Separated", "description": "Provision of Market Studies",
                      "reason": "related", "sub_categories": ["feasibility study", "Market Survey"]},
                     {"from": ["1"], "status": "Separated", "description": "Provision of Evaluations",
                      "reason": "related", "sub_categories": ["Baseline Survey", "Midterm Evaluation"]}],
           "additional": []}
    a, b = rf.check(raw, drafts, ["C"])
    assert a.sub_categories == ["Feasibility Study", "Market Survey"]          # the draft's wording
    assert b.sub_categories == ["Baseline Survey", "Midterm Evaluation"]
    assert "Separated into a few categories (usually 3-4)" in rf.SYSTEM


# --- D. the client's previous list -----------------------------------------------------------

def test_the_previous_list_is_category_names_only(book):
    p = read_previous(book, label="2025-2026")
    assert p.tabs == ["Prequalified Suppl. goods &serv", "Prequalified_Consultant"]
    assert p.names == ["Supply of general stationeries", "Hire of mobile toilets", "Provision of taxi services",
                       "Event management and coordination", "Statisticians"]
    assert not any(s.lower() in " ".join(p.names).lower() for s in SUPPLIERS + ["No category", "Amref"])


def test_a_draft_layout_tab_can_be_the_previous_list(book):
    p = read_previous(book, ["Catagory for Consultancy"])
    assert p.names[:2] == ["Project/Programme research evaluations", "Provision of event management and coordination services"]


def test_previous_categories_are_matched_or_proposed(book):
    drafts = rf.read_draft(book, TABS)
    p = read_previous(book, label="2025-2026")
    lines = rf.check(None, drafts, rf.groups_of(drafts), p)
    on = lambda i: next(ln for ln in lines if ln.from_draft == [i])
    assert on("1-1").previous == ["Supply of general stationeries"]
    assert on("2-2").previous == ["Event management and coordination"]      # across tabs
    added = [ln for ln in lines if ln.status == rf.ADDITIONAL]
    assert [(ln.description, ln.group) for ln in added] == [
        ("Hire of Mobile Toilets", rf.PREVIOUS_GROUP), ("Provision of Taxi Services", rf.PREVIOUS_GROUP),
        ("Statisticians", rf.PREVIOUS_GROUP)]
    assert added[0].reason == "In the client's 2025-2026 list (Prequalified Suppl. goods &serv), not in the draft"
    assert rf.INDIVIDUAL not in added[0].applies_to and rf.INDIVIDUAL in added[2].applies_to   # Statisticians (19 B)
    assert added[0].question == rf.PREVIOUS_ASK and lines[-1] is added[-1]


def test_the_model_may_find_a_previous_category_covered(book):
    drafts = rf.read_draft(book, TABS)
    p = read_previous(book, label="2025-2026")
    raw = {"lines": [], "additional": [{"description": "Hire of Mobile Toilets", "reason": "common"}],
           "previous_covered": [{"name": "statisticians", "by": "2-1"}, {"name": "Unknown", "by": "1-1"}]}
    lines = rf.check(raw, drafts, rf.groups_of(drafts), p)
    assert next(ln for ln in lines if ln.from_draft == ["2-1"]).previous == ["Statisticians (covered)"]
    added = [ln for ln in lines if ln.status == rf.ADDITIONAL]
    assert [ln.description for ln in added] == ["Hire of Mobile Toilets", "Provision of Taxi Services"]
    assert added[0].reason == ("common; In the client's 2025-2026 list (Prequalified Suppl. goods &serv), "
                               "not in the draft")                                              # the model's, once
    prompt = rf.build_prompt(drafts, [], None, "", p)
    assert "THE CLIENT'S PREVIOUS LIST (2025-2026)" in prompt and "Statisticians" in prompt
    assert "Supply of general stationeries\n" not in prompt                  # matched: not asked about


def test_no_supplier_reaches_the_revised_workbook_the_log_or_the_prompt(book, tmp_path, caplog):
    p = flows.read_previous(book, None, "2025-2026")
    drafts = rf.read_draft(book, TABS)
    out = tmp_path / "revised.xlsx"
    with caplog.at_level(logging.INFO, logger="tendersure"):
        flows.propose_file(book, "AAA", TABS, out=str(out), bank=str(tmp_path / "none"),
                           research=str(tmp_path / "r.csv"), model=flows.Model(off=True), previous=p)
    wb = openpyxl.load_workbook(out)
    text = " ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value)
    prompt = rf.build_prompt(drafts, [], None, "", p)
    for s in SUPPLIERS + ["info@acme.example", "+251900000000"]:
        assert s not in text and s not in caplog.text and s not in prompt
    assert "5 categories from the client's 2025-2026 list" in caplog.text
    _, lines = rf.read_revised(str(out))
    assert next(ln for ln in lines if ln.from_draft == ["1-1"]).previous == ["Supply of general stationeries"]
    assert any(ln.group == rf.PREVIOUS_GROUP and ln.status == rf.ADDITIONAL for ln in lines)


# --- spec 19: what Amref's first proposal showed ---------------------------------------------

def test_the_models_wording_suggests_eligibility_and_the_drafts_applies():
    drafts = [rf.Draft("1", "Provision of air ticketing services", "T"),
              rf.Draft("2", "Provision of travel agency services (IATA Registered Only)", "T")]
    raw = {"lines": [{"from": ["1"], "status": "Renamed", "reason": "standard",
                      "description": "Provision of Air Ticketing Services (IATA Registered Only)"},
                     {"from": ["2"], "status": "Kept", "description": "x"}],
           "additional": [{"description": "Provision of Courier Services (DHL Registered Only)", "reason": "common"}]}
    tickets, travel, courier = rf.check(raw, drafts, ["T"])
    assert (tickets.eligibility, tickets.suggested_eligibility) == ([], ["IATA Registration Certificate"])
    assert travel.eligibility == ["IATA Registration Certificate"]
    assert (courier.eligibility, courier.suggested_eligibility) == ([], ["DHL Registration Certificate"])


def test_a_previous_category_stated_eligibility_is_its_own():
    p = rf.Previous(["Provision of air ticketing services (IATA Registered Only)"], "2025-2026")
    added = rf.check(None, [rf.Draft("1", "Supply of stationery")], [None], p)[-1]
    assert added.eligibility == ["IATA Registration Certificate"]


def test_supplying_is_not_repairing():
    drafts = [rf.Draft("1", "Supply of cameras and filming equipment and related accessories"),
              rf.Draft("2", "Supply and maintenance of solar panels")]
    by, left = rf.previous_matches(drafts, [
        "Maintenance and repair of cameras and filming equipment and related accessories",
        "Supply, installation and maintenance of solar panels & related accessories",
        "Supply of cameras"])
    assert left == ["Maintenance and repair of cameras and filming equipment and related accessories"]
    assert by["2"] == ["Supply, installation and maintenance of solar panels & related accessories"]
    assert by["1"] == ["Supply of cameras"]


def test_a_suggested_split_keeps_a_sub_category_that_names_its_work():
    line = rf.Line("Provision of Research Consultancy", rf.KEPT, ["1"],
                   sub_categories=["Provision of Project-Based Consultancy Services", "Market Survey"])
    rf.suggest([line])
    assert line.suggested_split == ["Provision of Project-Based Consultancy Services", "Provision of Market Survey"]


def test_abbreviations_stay_lower_case():
    assert rf.title_case("Supply of materials, i.e. cement (e.g. pumps, etc.)") == \
        "Supply of Materials, i.e. Cement (e.g. Pumps, etc.)"
