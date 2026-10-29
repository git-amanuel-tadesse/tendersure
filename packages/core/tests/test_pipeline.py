import json
import re

import openpyxl
import pytest

from tendersure.categories import _split_sub_categories, category_kind
from tendersure.flows import load_corpus
from tendersure.generate import (Generator, MarksError, Target, apply_section_marks, check_total,
                                 fill_placeholder, swap_client)
from tendersure.parser import parse_workbook, section_key
from tendersure.selectors import ModelSelector
from tendersure.llm import LLMError, OpenRouterLLM, SchemaError, check_schema
from tendersure.category_llm import LLMExtractor, reconcile, verify
from tendersure.categories import Category
from tendersure.similarity import Retriever
from tendersure.validate import validate_sheet
from tendersure.writer import Writer

from tendersure.testing import GENERIC, TAIL, tech, write_sheet


def _gen(corpus):
    sheets = load_corpus(str(corpus))
    return sheets, Generator(sheets, Retriever(sheets))


# --- parser and validator ------------------------------------------------------

def test_parses_synthetic_corpus(corpus):
    sheets = load_corpus(str(corpus))
    assert len(sheets) == 4
    s = next(s for s in sheets if s.name == "AAA-ET-2026-001")
    assert s.client == "AAA" and s.description.startswith("Supply of Office")
    assert validate_sheet(s) == []


def test_reads_fields_by_header_in_the_21_column_layout(tmp_path):
    from tendersure.schema import LAYOUT
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(2, 2, "NNN_2027_001: Supply of ICT Equipment")
    for i, (_, label) in enumerate(LAYOUT):
        ws.cell(4, 2 + i, label)
    row = {"section": "Eligibility", "sub_section": "N/A", "criteria": "Please attach a valid Trade license",
           "marks": 0, "eligibility": True, "compliance": 0, "archive_short_name": "Trade License",
           "acronym": "TL", "answer_type": 5, "mandatory": True, "compliance_attach": True,
           "gate_question": True, "applies_to": "Sole Proprietor, Share Company"}
    col = {f: 2 + i for i, (f, _) in enumerate(LAYOUT)}
    for f, v in row.items():
        ws.cell(5, col[f], v)
    ws.cell(5, col["system"], 7)            # System sits in G here, where 14-column sheets have Compliance
    path = tmp_path / "new.xlsx"
    wb.save(path)

    r = parse_workbook(str(path))[0].rows[0]
    assert (r.marks, r.system, r.compliance) == (0, 7, 0)
    assert r.eligibility and r.compliance_attach and not r.attachment
    assert (r.archive_short_name, r.acronym, r.answer_type) == ("Trade License", "TL", 5)
    assert r.gate_question and r.applies_to == "Sole Proprietor, Share Company"


def test_unknown_header_labels_fall_back_to_14_column_positions(tmp_path):
    wb = openpyxl.Workbook()
    write_sheet(wb.active, "AAA-ET-2026-009", "Test", GENERIC + TAIL, "AAA")
    for c in range(3, 16):
        wb.active.cell(4, c, f"col {c}")    # only "Section" survives, so the sheet is still found
    wb.save(tmp_path / "odd.xlsx")
    r = next(r for r in parse_workbook(str(tmp_path / "odd.xlsx"))[0].rows if r.criteria == "Attach TIN certificate")
    assert (r.answer_type, r.archive_short_name, r.compliance_attach) == (5, "TIN", True)


def test_validator_catches_silent_and_arithmetic_errors(tmp_path):
    rows = GENERIC + tech("goods") + TAIL
    rows = [list(r) for r in rows]
    rows[6][8] = 5                      # 'Insert TIN number' coded as upload
    rows[3][8] = 1                      # 'Attach evidence of office' coded as text
    rows[11][13] = "0,15"               # years in business: best score 15, row worth 20
    wb = openpyxl.Workbook()
    write_sheet(wb.active, "AAA-ET-2026-009", "Test", [tuple(r) for r in rows], "AAA")
    ws = wb.active
    ws.cell(9, 5, 999)                  # first subtotal: wrong
    ws.cell(9, 2).value = None          # ... and its label moved to the Criteria column
    ws.cell(9, 4, "Sub Total")
    wb.save(tmp_path / "x.xlsx")
    s = parse_workbook(str(tmp_path / "x.xlsx"))[0]
    rules = {f.rule for f in validate_sheet(s)}
    assert {"AT-ASKS-TEXT", "AT-TEXT-SAYS-ATTACH", "OPT-MAX-MARKS", "SUM-ROWS", "SUM-LABEL-COLUMN"} <= rules
    assert all(r.criteria != "Sub Total" for r in s.rows)


def test_country_code_is_not_a_client(tmp_path):
    wb = openpyxl.Workbook()
    write_sheet(wb.active, "ET-2026_016", "Research", GENERIC + TAIL, "X")
    wb.save(tmp_path / "x.xlsx")
    s = parse_workbook(str(tmp_path / "x.xlsx"))[0]
    assert s.client is None
    assert "ID-NO-CLIENT" in {f.rule for f in validate_sheet(s)}


# --- generation ----------------------------------------------------------------

def test_text_adaptation_keeps_platform_name():
    assert swap_client("Are you a supplier of ILRI?", "ERCS") == "Are you a supplier of ERCS?"
    assert swap_client("I authorize TenderSure to share", "ERCS") == "I authorize TenderSure to share"
    assert fill_placeholder("Are you a current consultant of ?", "SNV") == "Are you a current consultant of SNV?"


def test_plan_reuses_client_skeleton_and_flags_category_rows(corpus):
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-004", "Provision of Cleaning Services", "AAA", client_name="AAA"))
    assert p.skeleton.sheet.name == "AAA-ET-2026-003"
    assert p.total() == 100
    check_total(p)
    flagged = [q.row.criteria for q in p.questions if q.needs_reading]
    assert flagged and all(section_key(q.row.section) == "technical"
                           for q in p.questions if q.needs_reading)
    assert p.findings == []


def test_plan_from_other_client_swaps_names_and_suggests(corpus):
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    texts = [q.row.criteria for q in p.questions]
    assert not any("BBB" in t for t in texts)
    assert any("food safety" in s.row.criteria for s in p.suggestions)


def test_existing_code_is_reused_unflagged(corpus):
    sheets, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-002", "Supply of Electronics and Home Appliances (revised)", "AAA"))
    assert p.skeleton.sheet.name == "AAA-ET-2026-002"
    assert not any(q.needs_reading for q in p.questions)
    assert p.notes and "revised" in p.notes[0]


def test_marks_split_and_total_enforcement(corpus):
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-004", "Supply of Cleaning Materials", "AAA"))
    apply_section_marks(p, {"technical": 30, "trade_reference": 40})
    check_total(p)
    years = next(q.row for q in p.questions if q.row.criteria == "Years in business")
    assert years.option_scores == "0,20"
    with pytest.raises(MarksError):
        apply_section_marks(p, {"technical": 40})
        check_total(p)


def test_corrects_unambiguous_answer_type_errors(tmp_path):
    rows = [list(r) for r in GENERIC + tech("goods") + TAIL]
    rows[3][8] = 1                       # 'Attach evidence of office' as a text box
    wb = openpyxl.Workbook()
    write_sheet(wb.active, "AAA-ET-2026-001", "Supply of Stationery", [tuple(r) for r in rows], "AAA")
    wb.save(tmp_path / "a.xlsx")
    sheets = load_corpus(str(tmp_path))
    p = Generator(sheets, Retriever(sheets)).plan(Target("AAA-ET-2026-001", "Supply of Stationery", "AAA"))
    q = next(q for q in p.questions if q.row.criteria.startswith("Attach evidence of physically"))
    assert q.row.answer_type == 5 and q.origin == "adapted"


# --- writer --------------------------------------------------------------------

def test_writer_round_trip_is_lossless(corpus, tmp_path):
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    out = tmp_path / "out.xlsx"
    Writer().write([p], str(out))
    back = parse_workbook(str(out))[0]
    fields = ["section", "sub_section", "criteria", "marks", "system", "compliance", "answer_type",
              "mandatory", "attachment", "compliance_attach", "options", "option_scores"]
    assert [[getattr(r, f) for f in fields] for r in back.rows] == \
           [[getattr(r, f) for f in fields] for r in src.rows]
    assert [(s.label, s.marks) for s in back.structural] == [(s.label, s.marks) for s in src.structural]
    assert validate_sheet(back) == []


def test_writer_uses_the_21_column_template(corpus, tmp_path):
    from tendersure.schema import LAYOUT
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")     # a 14-column skeleton
    p = g.plan(Target(src.code, src.description, src.client))
    q = p.questions[1].row                                            # "Company name"
    q.eligibility, q.gate_question, q.applies_to = True, True, "Sole Proprietor, Share Company"
    out = tmp_path / "out.xlsx"
    Writer().write([p], str(out))

    ws = openpyxl.load_workbook(out).active
    assert [ws.cell(4, 2 + i).value for i in range(len(LAYOUT))] == [label for _, label in LAYOUT]
    assert "E3:I3" in {str(m) for m in ws.merged_cells.ranges}
    back = parse_workbook(str(out))[0].rows[1]
    assert (back.eligibility, back.gate_question, back.applies_to) == \
           (True, True, "Sole Proprietor, Share Company")


def test_writer_copies_styles_by_field_not_position(corpus, tmp_path):
    from openpyxl.styles import PatternFill
    path = corpus / "AAA.xlsx"
    wb = openpyxl.load_workbook(path)
    ws = wb["AAA-ET-2026-003"]                     # 14 columns: Compliance in G, Mandatory in K
    ws["G4"].fill = PatternFill("solid", fgColor="FFFF0000")
    ws["K4"].fill = PatternFill("solid", fgColor="FF0000FF")
    ws.column_dimensions["G"].width = 33
    wb.save(path)

    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    Writer().write([g.plan(Target(src.code, src.description, src.client))], str(tmp_path / "o.xlsx"))
    out = openpyxl.load_workbook(tmp_path / "o.xlsx").active
    assert out["H4"].value == "Compliance" and out["H4"].fill.fgColor.rgb == "FFFF0000"
    assert out.column_dimensions["H"].width == 33
    assert out["F4"].value == "Eligibility" and out["F4"].fill.fgColor.rgb == "FF0000FF"   # Mandatory's


def test_marks_are_repeated_in_system_or_compliance(corpus):
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_template
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    upload = p.questions[3].row                             # "Attach evidence of physically registered office"
    upload.compliance = None                                # an upload with its marks in Marks only
    assert any(f.rule == "MARKS-ONE-COLUMN" for f in check_template(to_sheet(p)))

    finalise(p)
    assert not [f for f in check_template(to_sheet(p)) if f.rule == "MARKS-ONE-COLUMN"]
    rows = {q.row.criteria: q.row for q in p.questions}
    assert (rows["Company name"].system, rows["Company name"].compliance) == (0, None)
    assert (upload.marks, upload.system, upload.compliance) == (10, None, 10)
    assert p.total() == 100


def test_inserted_rows_keep_the_sub_section_block_whole(corpus, tmp_path):
    from tendersure.generate import insert_rows
    from tendersure.parser import Row
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    drafted = Row("", "", 0, criteria="Do you carry liability insurance? Yes/No", marks=0, answer_type=4)
    donor = Row("", "", 0, criteria="Attach your service licence", marks=0, answer_type=5,
                sub_section="Licences", compliance_attach=True)
    insert_rows(p, "technical", [drafted, donor], "newly drafted", "model draft", "test")
    assert drafted.sub_section == donor.sub_section == "N/A"

    Writer().write([p], str(tmp_path / "o.xlsx"))
    ws = openpyxl.load_workbook(tmp_path / "o.xlsx").active
    tech = [r for r in range(5, ws.max_row + 1)
            if ws.cell(r, 4).value in ("Do you hold a valid service licence? Yes/No",
                                       "Attach your service licence")]
    blocks = [m for m in ws.merged_cells.ranges if m.min_col == m.max_col == 3]
    assert any(m.min_row == tech[0] and m.max_row == tech[-1] for m in blocks)


def test_every_cell_and_merged_block_is_bordered(corpus, tmp_path):
    from tendersure.schema import LAYOUT
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")     # no styling at all
    Writer().write([g.plan(Target(src.code, src.description, src.client))], str(tmp_path / "o.xlsx"))
    ws = openpyxl.load_workbook(tmp_path / "o.xlsx").active
    owner = {(r, c): m for m in ws.merged_cells.ranges
             for r in range(m.min_row, m.max_row + 1) for c in range(m.min_col, m.max_col + 1)}
    open_edges = []
    for r in range(4, ws.max_row + 1):
        for c in range(2, 2 + len(LAYOUT)):
            m = owner.get((r, c))
            outer = {"left": not m or c == m.min_col, "right": not m or c == m.max_col,
                     "top": not m or r == m.min_row, "bottom": not m or r == m.max_row}
            open_edges += [(r, c, side) for side, needed in outer.items()
                           if needed and not getattr(ws.cell(r, c).border, side).style]
    assert open_edges == []


SUBS = ["Ground Coffee", "Tea", "Sugar, Milk Powder"]


def _plan_with_subs(corpus, subs, copied_options=None):
    """A plan for a category with `subs`; `copied_options` puts a sub-category
    question from another category into the skeleton."""
    from tendersure.generate import PlannedRow
    from tendersure.parser import Row
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client, sub_categories=subs))
    if copied_options:
        org = next(i for i, q in enumerate(p.rows) if q.kind == "question" and q.row.criteria == "Organization type")
        p.rows.insert(org + 1, PlannedRow(kind="question", row=Row(
            "", "", 0, section="Company Information", sub_section="Information", answer_type=3, mandatory=True,
            criteria="Please select the subcategory you would like to participate in",
            options=copied_options, option_scores="0,0,0")))
    return p


def test_sub_category_question_is_added_after_organization_type(corpus):
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_sub_category_question
    p = _plan_with_subs(corpus, SUBS)
    finalise(p)
    texts = [q.row.criteria for q in p.questions]
    i = texts.index("Please select the subcategory you would like to participate in")
    assert texts[i - 1] == "Organization type"
    r = p.questions[i].row
    assert (r.section, r.sub_section, r.answer_type, r.mandatory) == ("Company Information", "Information", 3, True)
    assert r.option_list() == ["Ground Coffee", "Tea", "Sugar / Milk Powder"]   # the comma would split it
    assert (r.option_scores, r.marks, r.system) == ("0,0,0", 0, 0)
    assert check_sub_category_question(to_sheet(p), SUBS) == []
    assert p.total() == 100


def test_copied_sub_category_question_takes_this_categorys_options(corpus):
    from tendersure.generate import finalise
    p = _plan_with_subs(corpus, SUBS, copied_options="Tyres and Tubes, Batteries, Lubricants")
    finalise(p)
    qs = [q for q in p.questions if "subcategory" in q.row.criteria]
    assert len(qs) == 1 and qs[0].row.options == "Ground Coffee, Tea, Sugar / Milk Powder"
    assert any("were: Tyres and Tubes" in n for n in qs[0].notes)


def test_drafted_sub_category_question_moves_into_place(corpus):
    from tendersure.generate import finalise, insert_rows
    from tendersure.parser import Row
    p = _plan_with_subs(corpus, SUBS)
    drafted = Row("", "", 0, criteria="Please select the sub-categories of grocery items you supply",
                  answer_type=3, options="Coffee, Tea", option_scores="0,0", marks=0)
    insert_rows(p, "technical", [drafted], "newly drafted", "model draft", "drafted by the model")
    finalise(p)
    texts = [q.row.criteria for q in p.questions]
    assert texts.count("Please select the subcategory you would like to participate in") == 1
    assert not any("grocery items you supply" in t for t in texts)
    i = texts.index("Please select the subcategory you would like to participate in")
    assert texts[i - 1] == "Organization type" and p.questions[i].row.section == "Company Information"
    assert any("moved from 'Technical" in n for n in p.questions[i].notes)


def test_sub_category_question_goes_when_the_category_has_none(corpus):
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_sub_category_question
    p = _plan_with_subs(corpus, [], copied_options="Tyres and Tubes, Batteries, Lubricants")
    assert [f.rule for f in check_sub_category_question(to_sheet(p), [])] == ["SUBCAT-UNEXPECTED"]
    finalise(p)
    assert not any("subcategory" in q.row.criteria for q in p.questions)
    assert "advert gives this one none" in p.dropped[-1][1]


def _trade_license(marks=0.0, section="Registration and Statutory Requirements"):
    from tendersure.parser import Row
    return Row("", "", 0, section=section, sub_section="N/A", criteria="Please attach a valid Trade license",
               marks=marks, compliance=marks, answer_type=1, archive_short_name="Business Permit", acronym="BP")


def test_trade_license_moves_to_an_eligibility_section(corpus, tmp_path):
    from tendersure.generate import finalise
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    tin = next(i for i, q in enumerate(p.rows) if q.kind == "question" and q.row.criteria == "Insert TIN number")
    from tendersure.generate import PlannedRow
    p.rows.insert(tin, PlannedRow(kind="question", row=_trade_license()))
    finalise(p)

    order = []
    for q in p.questions:
        if not order or order[-1] != q.row.section:
            order.append(q.row.section)
    assert order[:4] == ["Declaration", "Company Information", "Eligibility", "Registration and Statutory Requirements"]
    tl = next(q.row for q in p.questions if q.row.criteria == "Please attach a valid Trade license")
    assert (tl.eligibility, tl.answer_type, tl.mandatory, tl.compliance_attach) == (True, 5, True, True)
    assert (tl.archive_short_name, tl.acronym) == ("Business Permit", "BP")     # the client's own names kept
    assert [q.row.criteria for q in p.questions if q.row.eligibility] == ["Please attach a valid Trade license"]
    assert p.total() == 100

    from tendersure.generate import to_sheet
    from tendersure.validate import check_template
    elig = lambda: [f.rule for f in check_template(to_sheet(p)) if f.rule.startswith("ELIG")]
    assert elig() == []
    next(q.row for q in p.questions if q.row.criteria == "Attach TIN certificate").eligibility = True
    assert elig() == ["ELIG-OUTSIDE"]
    next(q.row for q in p.questions if q.row.criteria == "Attach TIN certificate").eligibility = False

    Writer().write([p], str(tmp_path / "o.xlsx"))
    ws = openpyxl.load_workbook(tmp_path / "o.xlsx").active
    labels = [ws.cell(r, 2).value for r in range(5, ws.max_row + 1) if ws.cell(r, 2).value]
    assert labels[:5] == ["Declaration", "Company Information", "Sub Total", "Eligibility",
                          "Registration and Statutory Requirements"]


def test_sheet_without_trade_license_gets_the_standard_row(corpus):
    from tendersure.generate import finalise
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    finalise(p)
    elig = [q for q in p.questions if q.row.eligibility]
    assert [q.row.criteria for q in elig] == ["Please attach a valid Trade license"]
    assert elig[0].row.compliance == 0 and elig[0].source == "standard Trade License row"


def test_trade_license_with_marks_stays_and_is_flagged(corpus):
    from tendersure.generate import PlannedRow, finalise
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    tl = PlannedRow(kind="question", row=_trade_license(marks=0.0, section="Technical Specifications-TECH"))
    tl.row.marks = 2.0                                       # an ILRI-style scored Trade License
    tech = next(i for i, q in enumerate(p.rows) if q.kind == "question" and q.row.section.startswith("Technical"))
    p.rows.insert(tech, tl)
    finalise(p)
    assert tl.row.section == "Technical Specifications-TECH" and not tl.row.eligibility
    assert tl.needs_reading and "not moved to Eligibility" in tl.notes[-1]
    assert not any(q.row.eligibility for q in p.questions)


def test_writer_refuses_off_total(corpus, tmp_path):
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-004", "Supply of Cleaning Materials", "AAA"))
    p.questions[3].row.marks = 50
    with pytest.raises(MarksError):
        Writer().write([p], str(tmp_path / "x.xlsx"))


# --- model access (no network) -----------------------------------------------

def _no_brief(payload):
    """A canned sheet proposal answers as a model does without a client's
    brief (spec 07): nothing from the brief, nothing left unapplied."""
    if isinstance(payload, dict) and {"drop", "reword", "add", "drafts"} <= set(payload):
        payload = {**payload, "brief_not_applied": payload.get("brief_not_applied", [])}
        for k in ("drop", "reword", "add", "drafts"):
            payload[k] = [{"from_brief": False, **x} for x in payload[k]]
    return payload


class FakeTransport:
    """Stands in for OpenRouter: records requests, replies with a canned payload."""

    def __init__(self, payload, model_used=None, finish="stop", raw=None):
        self.payload, self.model_used, self.finish, self.raw = _no_brief(payload), model_used, finish, raw
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        text = self.raw if self.raw is not None else json.dumps(self.payload)
        return {"model": self.model_used or body["model"],
                "choices": [{"finish_reason": self.finish, "message": {"content": text}}]}


SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}, "k": {"type": "string", "enum": ["a", "b"]}},
          "required": ["n", "k"]}


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5.5", "google/gemini-4-argon"])
def test_switching_provider_is_one_string(model):
    t = FakeTransport({"n": 1, "k": "a"})
    llm = OpenRouterLLM(model=model, fallbacks=["openai/gpt-5"], transport=t)
    assert llm.complete_json("sys", "hi", SCHEMA, "x") == {"n": 1, "k": "a"}
    url, headers, body = t.calls[0]
    assert url.endswith("/chat/completions") and body["model"] == model
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["models"] == ["openai/gpt-5"]
    assert body["provider"]["data_collection"] == "deny" and body["provider"]["zdr"] is True
    assert llm.last_model == model


def test_prompt_json_mode_and_fenced_reply():
    t = FakeTransport(None, raw='Here you go:\n```json\n{"n": 2, "k": "b"}\n```')
    llm = OpenRouterLLM(model="some/model", json_mode="prompt", transport=t)
    assert llm.complete_json("sys", "hi", SCHEMA, "x") == {"n": 2, "k": "b"}
    body = t.calls[0][2]
    assert "response_format" not in body and "JSON Schema" in body["messages"][0]["content"]


def test_replies_are_checked_not_trusted():
    with pytest.raises(SchemaError):
        OpenRouterLLM(model="m", transport=FakeTransport({"n": "1", "k": "c"})).complete_json("s", "p", SCHEMA, "x")
    with pytest.raises(LLMError):
        OpenRouterLLM(model="m", transport=FakeTransport({"n": 1, "k": "a"}, finish="length")).complete_json("s", "p", SCHEMA, "x")
    assert check_schema({"n": True, "k": "a"}, SCHEMA) == ["$.n: expected integer, got bool"]


def test_model_proposes_and_review_applies(corpus, tmp_path):
    from tendersure.reports import write_qa_report
    from tendersure.review import apply_answers, decision_items, read_answers, read_proposals
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    flagged = [i for i, r in enumerate(p.rows) if r.kind == "question" and r.needs_reading]
    unflagged = next(i for i, r in enumerate(p.rows) if r.kind == "question" and not r.needs_reading)
    food = next(j for j, s in enumerate(p.suggestions) if "food safety" in s.row.criteria)
    t = FakeTransport({
        "reword": [],
        "drop": [{"row": flagged[0], "reason": "not catering"}, {"row": unflagged, "reason": "x"}],
        "add": [{"suggestion": food, "reason": "catering needs it"}, {"suggestion": 999, "reason": "x"}],
        "drafts": [{"section": "registration", "criteria": "Attach a valid food handling licence",
                    "answer_type": 5, "options": "", "reason": "category licence"},
                   {"section": "technical", "criteria": "Bad type", "answer_type": 9,
                    "options": "", "reason": "x"}],
    }, model_used="google/gemini-4-argon")
    prop = ModelSelector(llm=OpenRouterLLM(model="google/gemini-4-argon", transport=t)).propose(p)
    assert len(prop["drop"]) == 1 and len(prop["add"]) == 1 and len(prop["drafts"]) == 1
    assert len(prop["ignored"]) == 3                       # unflagged drop, unknown suggestion, bad type
    n_before = len(p.questions)
    assert len(p.questions) == n_before                    # proposing changes nothing

    items = decision_items([p])
    q = next(i for i in items if i.type == "question")
    a = next(i for i in items if i.id == "A-AAA-ET-2026-031")
    assert q.proposed == "remove all" and "gemini" in q.why
    assert a.proposed == "add all" and "NEW, drafted" in a.decide

    # The QA report keeps the proposal; the review replays it without the model.
    path = tmp_path / "qa.xlsx"
    write_qa_report([p], str(path))
    assert read_proposals(str(path))["AAA-ET-2026-031"] == prop
    p2 = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    p2.proposal = read_proposals(str(path))["AAA-ET-2026-031"]
    out_items, log, problems = apply_answers([p2], {a.id: "add: 2"})     # Q left blank: accepted
    texts = [x.row.criteria for x in p2.questions]
    assert "Attach a valid food handling licence" in texts
    assert not any("food safety" in x for x in texts)      # item 1 was not picked
    assert len(p2.questions) == n_before - 1 + 1
    drafted = next(x for x in p2.questions if x.origin == "newly drafted")
    assert drafted.row.marks == 0 and drafted.needs_reading and drafted.row.attachment
    assert next(i for i in out_items if i.type == "question").status == "applied: removed from 1 (proposal accepted)"
    check_total(p2)
    assert len(t.calls) == 1


def test_weak_match_without_model_offers_suggestions(corpus):
    from tendersure.review import decision_items
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    assert p.weak_match
    a = next(i for i in decision_items([p]) if i.id == "A-AAA-ET-2026-031")
    assert a.proposed == "skip" and "food safety" in a.decide


# --- model-assisted category extraction ----------------------------------------

def _cats():
    return [Category(1, "Supply of Stationery and Toners", "GENERAL", ["Supply of office Stationery"]),
            Category(2, "Supply of Electronics/ Home Appliances (TVs, Fridges)", "GENERAL"),
            Category(3, "Provision of Catering Services", "HOSPITALITY")]


VOCAB = {t for c in _cats() for t in re.findall(r"[a-z0-9]+", (c.description + " " + " ".join(c.sub_categories)
                                                                + " " + c.group).lower())}


def test_verify_flags_words_not_in_the_pdf():
    cats = _cats()
    cats[1] = Category(2, "Supply of Electronics and Home Appliances (TVs, Refrigerators)", "GENERAL")
    del cats[2]
    issues = verify(cats + [Category(4, "Provision of Catering Services", "HOSPITALITY")], VOCAB)
    msgs = [(i.kind, i.number, i.message) for i in issues]
    assert ("sequence", None, "missing category numbers: [3]") in msgs
    assert any(k == "unverified" and n == 2 and "refrigerators" in m for k, n, m in msgs)
    assert verify(_cats(), set())[0].kind == "no-text-layer"


def test_reconcile_prefers_verbatim_text():
    layout = _cats()
    layout[2] = Category(3, "Provision of Catering Servces", "HOSPITALITY")      # layout slip
    model = _cats()
    model[1] = Category(2, "Supply of Electronics and Home Appliances (TVs, Refrigerators)", "GENERAL")
    merged, issues = reconcile(layout, model, VOCAB)
    by = {c.number: c for c in merged}
    assert by[2].description == _cats()[1].description      # layout verbatim, model paraphrased
    assert by[3].description == "Provision of Catering Services"   # model verbatim, layout slipped
    assert len([i for i in issues if i.kind == "disagreement"]) == 2


def test_llm_extractor_sends_anonymised_text_not_the_pdf(tmp_path, monkeypatch):
    import tendersure.category_llm as cl
    monkeypatch.setattr(cl, "notice_text", lambda path: (
        "The Ethiopian Red Cross Society (ERCS) invites vendors. Contact clearskies@tendersure.africa "
        "or +251 955 210 999.\n1 Supply of Stationery and Toners"))
    payload = {"notice_title": "Notice", "client_name": "[CLIENT]", "closing_date": "September 10, 2026",
               "categories": [{"number": c.number, "description": c.description, "group": c.group,
                               "sub_categories": c.sub_categories, "kind": "goods"} for c in _cats()]}
    t = FakeTransport(payload)
    data = LLMExtractor(llm=OpenRouterLLM(model="google/gemini-4-argon", transport=t)).ask("n.pdf", "ERCS")
    sent = json.dumps(t.calls[0][2])
    for secret in ("Red Cross", "ERCS", "clearskies", "tendersure", "955 210"):
        assert secret.lower() not in sent.lower()
    assert '"type": "file"' not in sent                      # never the PDF itself
    assert data["client_name"] == "ERCS"                    # placeholders restored locally


# --- category extraction helpers -----------------------------------------------

def test_sub_category_wrapping():
    lines = [(90, "Supply of office Stationery"), (106, "Supply of Toners and"), (121, "Cartridges"),
             (324, "Sugar, Milk Powder"), (339, "Fresh fruits, Vegetables, Tubers"), (352, "and Herbs")]
    assert _split_sub_categories(lines) == [
        "Supply of office Stationery", "Supply of Toners and Cartridges",
        "Sugar, Milk Powder", "Fresh fruits, Vegetables, Tubers and Herbs"]


@pytest.mark.parametrize("desc,kind", [
    ("Supply of Assorted Pharmaceuticals", "goods"),
    ("Supply, Installation and Maintenance of Generators", "goods+service"),
    ("Provision of Catering Services", "service"),
    ("Provision of Consultancy Services for Design of Buildings", "consultancy"),
    ("Provision of Civil Works in Building Construction", "works"),
    ("Supply of General Construction/Building Materials, wood works", "goods"),
])
def test_category_kind(desc, kind):
    assert category_kind(desc) == kind


def test_category_table_keeps_eligibility_gate_and_applies_to(tmp_path):
    from tendersure.categories import load, save
    cats = [Category(43, "Provision of Travel Agency Services (IATA Registered Only)", "SERVICES",
                     eligibility=["Trade License", "IATA Registration Certificate"],
                     applies_to=["Sole Proprietor", "Private Limited Company", "Share Company"]),
            Category(39, "Provision of Consultancy Services", "CONSULTANCY", gate_question=True,
                     applies_to=["Individual Consultant", "Private Limited Company"])]
    for name in ("cats.csv", "cats.json"):
        save(cats, str(tmp_path / name))
        assert load(str(tmp_path / name)) == cats

    old = tmp_path / "old.csv"            # a table written before these columns existed
    old.write_text("number,group,description,sub_categories,kind,client,version\n"
                   "1,GENERAL,Supply of Stationery,,goods,ERCS,v1\n")
    c = load(str(old))[0]
    assert (c.eligibility, c.gate_question, c.applies_to) == ([], False, [])


# --- real corpus (skipped without client data) ---------------------------------

def test_real_notice_sub_categories_are_all_read(real_corpus):
    import glob
    from tendersure.categories import extract_categories
    pdfs = glob.glob(f"{real_corpus}/ERCS*Invitation*.pdf")
    if not pdfs:
        pytest.skip("ERCS notice not present")
    cats = {c.number: c.sub_categories for c in extract_categories(pdfs[0], "ERCS")}
    # The notice's SUB-CATEGORY column, as printed (checked by hand, 2026-10-28).
    assert {n: subs for n, subs in cats.items() if subs} == {
        1: ["Supply of office Stationery", "Supply of Toners and Cartridges"],
        16: ["Laundry and Body Soaps", "Mattresses, Blankets, sleeping Matts and Bedsheets, Trapulin",
             "Jerry Can, Bucket, Washing Basin", "Sanitary pads and Dignity Kits", "Kitchen Sets"],
        34: ["Ground Coffee", "Tea", "Sugar, Milk Powder", "Fresh fruits, Vegetables, Tubers and Herbs",
             "Ethiopian Traditional Ingredients (Spices, Ghee Etc.)", "Dairy Products"],
        39: ["Motor Vehicle Tyres and Tubes", "Batteries", "Lubricants"],
        42: ["Feasibility Study and Assessment", "Market survey", "Business plan",
             "Monitoring and Evaluation on base line, Midterm, End term", "Gender and Social Inclusion"],
    }


def test_real_corpus_round_trip(real_corpus, tmp_path):
    sheets = load_corpus(real_corpus)
    g = Generator(sheets, Retriever(sheets))
    src = next(s for s in sheets if s.name == "ERCS-ET-2026-002")
    p = g.plan(Target(src.code, src.description, src.client))
    Writer().write([p], str(tmp_path / "o.xlsx"))
    back = parse_workbook(str(tmp_path / "o.xlsx"))[0]
    assert [(r.criteria, r.marks, r.answer_type, r.options) for r in back.rows] == \
           [(r.criteria, r.marks, r.answer_type, r.options) for r in src.rows]


# --- review loop ---------------------------------------------------------------

def _batch(corpus):
    from tendersure.review import decision_items
    _, g = _gen(corpus)
    plans = [g.plan(Target(f"AAA-ET-2026-0{n}", d, "AAA")) for n, d in
             ((10, "Provision of Cleaning Services"), (11, "Provision of Gardening Services"))]
    return plans, decision_items(plans)


def test_repeated_flags_collapse_into_one_decision(corpus):
    plans, items = _batch(corpus)
    flagged = sum(q.needs_reading for p in plans for q in p.questions)
    q_items = [i for i in items if i.type == "question"]
    assert flagged == 4 and len(q_items) == 1          # 2 questions x 2 categories -> 1 bundle
    assert q_items[0].codes == ["AAA-ET-2026-010", "AAA-ET-2026-011"]
    assert len([i for i in items if i.type == "marks"]) == 1


def test_review_answers_are_applied_and_reported(corpus, tmp_path):
    from tendersure.reports import write_qa_report
    from tendersure.review import apply_answers, read_answers
    plans, items = _batch(corpus)
    q = next(i for i in items if i.type == "question")
    m = next(i for i in items if i.type == "marks")
    answers = {q.id: "remove: 10", m.id: "experience 30, trade_reference 40", "Q-gone": "remove all"}
    out_items, log, problems = apply_answers(plans, answers)
    p10, p11 = plans
    assert not any(section_key(x.row.section) == "technical" for x in p10.questions)
    assert any(section_key(x.row.section) == "technical" for x in p11.questions)
    for p in plans:
        check_total(p)
    subtotals = lambda p: sum(r.kind == "subtotal" for r in p.rows)
    assert subtotals(p10) == subtotals(p11) - 1          # empty section's subtotal removed
    freed = next(i for i in out_items if i.id.startswith("M-FREED-technical"))
    assert freed.codes == ["AAA-ET-2026-010"] and freed.status == ""
    assert any("no longer in this batch" in x for x in problems)
    # the QA report carries the answers and statuses forward under the same ids
    path = tmp_path / "qa.xlsx"
    write_qa_report(plans, str(path), out_items)
    assert read_answers(str(path))[q.id] == "remove: 10"


def test_rejected_marks_answer_keeps_sheet_writable(corpus):
    from tendersure.review import apply_answers
    plans, items = _batch(corpus)
    m = next(i for i in items if i.type == "marks")
    out_items, _, problems = apply_answers(plans, {m.id: "trade_reference 90"})
    it = next(i for i in out_items if i.id == m.id)
    assert it.status.startswith("NOT APPLIED") and problems
    for p in plans:
        check_total(p)


# --- trust: grading, conventions, standing rulings -----------------------------

def _client_corpus(tmp_path, n=6, odd_one=None, broken=None):
    """n sheets for client AAA; `odd_one` gets 'Company name' mandatory; `broken` has bad arithmetic."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i in range(1, n + 1):
        code = f"AAA-ET-2026-{i:03d}"
        rows = [list(r) for r in GENERIC + tech("service" if i % 2 else "goods") + TAIL]
        if i == odd_one:
            rows[1][9] = True
        write_sheet(wb.create_sheet(code), code, f"Provision of Service Number {i}", [tuple(r) for r in rows], "AAA")
        if i == broken:
            wb[code].cell(9, 5, 99)            # subtotal no longer matches its rows
    wb.save(tmp_path / "c.xlsx")
    return load_corpus(str(tmp_path), cache=False)


def test_broken_sheets_are_quarantined_and_never_copied(tmp_path):
    from tendersure import trust
    sheets = _client_corpus(tmp_path, broken=1)
    grades = trust.grade(sheets)
    assert grades["AAA-ET-2026-001"].tier == "quarantined" and "SUM-ROWS" in grades["AAA-ET-2026-001"].reasons
    g = Generator(sheets, Retriever(sheets), grades=grades)
    p = g.plan(Target("AAA-ET-2026-009", "Provision of Service Number 1", "AAA"))
    assert p.skeleton.sheet.name != "AAA-ET-2026-001"
    assert any(n.startswith("skipped AAA-ET-2026-001 (quarantined") for n in p.notes)


def test_client_convention_corrects_copy_errors(tmp_path):
    from tendersure import trust
    sheets = _client_corpus(tmp_path, n=10, odd_one=3)  # 9 of 10 sheets: 'Company name' optional
    g = Generator(sheets, Retriever(sheets), grades=trust.grade(sheets))
    p = g.plan(Target("AAA-ET-2026-003", "Provision of Service Number 3", "AAA"))
    q = next(q for q in p.questions if q.row.criteria == "Company name")
    assert q.row.mandatory is False and "AAA convention (9 of 10 uses)" in q.notes[-1]


def test_standing_rulings_round_trip_and_win(tmp_path):
    from tendersure import trust
    path = str(tmp_path / "rulings.csv")
    trust.save_rulings(path, [{"id": "C-1", "client": "AAA", "question_key": "company name",
                               "field": "mandatory", "value": "True", "decided_on": "2026-10-16",
                               "question": "Company name"}])
    rulings, by_id = trust.load_rulings(path)
    assert rulings == {("AAA", "company name", "mandatory"): True} and "C-1" in by_id
    sheets = _client_corpus(tmp_path)
    p = Generator(sheets, Retriever(sheets), rulings=rulings).plan(
        Target("AAA-ET-2026-002", "Provision of Service Number 2", "AAA"))
    assert next(q for q in p.questions if q.row.criteria == "Company name").row.mandatory is True
    with pytest.raises(ValueError):
        trust.parse_value("answer_type", "7")


def test_schema_trouble_is_retried_once_in_prompt_mode():
    replies = iter(['{"n": "one", "k": "a"}', '{"n": 1, "k": "a"}'])     # bent, then right

    def transport(url, headers, body):
        transport.bodies.append(body)
        return {"model": body["model"], "choices": [{"finish_reason": "stop",
                                                     "message": {"content": next(replies)}}]}
    transport.bodies = []
    llm = OpenRouterLLM(model="google/gemini-4-argon", transport=transport)
    assert llm.complete_json("s", "p", SCHEMA, "x") == {"n": 1, "k": "a"}
    assert "response_format" in transport.bodies[0] and "response_format" not in transport.bodies[1]
    assert llm.retried == 1


def test_auth_errors_are_not_retried():
    def transport(url, headers, body):
        transport.n += 1
        raise LLMError("OpenRouter HTTP 401: invalid key")
    transport.n = 0
    with pytest.raises(LLMError):
        OpenRouterLLM(model="m", transport=transport).complete_json("s", "p", SCHEMA, "x")
    assert transport.n == 1


def test_key_and_model_from_dotenv(tmp_path, monkeypatch):
    from tendersure.llm import load_dotenv
    env = tmp_path / ".env"
    env.write_text('# comment\nexport OPENROUTER_API_KEY="sk-or-test"\nTENDERSURE_MODEL=google/gemini-3.8-flash\n')
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TENDERSURE_MODEL", "already/set")
    load_dotenv(str(env))
    import os
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test"
    assert os.environ["TENDERSURE_MODEL"] == "already/set"        # existing variables win


# --- privacy -------------------------------------------------------------------

def test_anonymiser_round_trip_and_guard():
    from tendersure.privacy import Anonymiser, PrivacyError
    a = Anonymiser("ERCS")
    raw = ("Is your company a current supplier of ERCS? Seen in ILRI-ET-078 for ILRI. "
           "Email procurement@redcross.org, call +251 911 234 567. Period 2026-2029, ISO/IEC 27001.")
    out = a.anonymise(raw)
    assert "ERCS" not in out and "ILRI" not in out and "redcross" not in out and "911 234" not in out
    assert "[CLIENT]" in out and "[CLIENT-" in out and "2026-2029" in out and "27001" in out
    assert a.leaks(out) == []
    assert a.restore(out).startswith("Is your company a current supplier of ERCS?")
    with pytest.raises(PrivacyError):
        a.guard(raw)


def test_model_never_sees_client_names(corpus):
    """A real proposal prompt mixes clients; nothing identifying may reach the provider."""
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    t = FakeTransport({"drop": [], "reword": [], "add": [], "drafts": [
        {"section": "technical", "criteria": "Do you supply [CLIENT] daily?", "answer_type": 4,
         "options": "", "reason": "x"}]})
    prop = ModelSelector(llm=OpenRouterLLM(model="m", transport=t)).propose(p)
    sent = json.dumps(t.calls[0][2])
    assert "BBB" not in sent and "AAA-ET" not in sent and "BBB-ET-001" not in sent
    assert prop["drafts"][0]["criteria"] == "Do you supply AAA daily?"


def test_raw_pdfs_are_refused():
    from tendersure.privacy import PrivacyError
    with pytest.raises(PrivacyError):
        OpenRouterLLM(model="m", transport=FakeTransport({})).complete_json("s", "p", SCHEMA, "x", pdf=b"%PDF")


def test_outbox_records_exactly_what_was_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("TENDERSURE_OUTBOX", str(tmp_path / "outbox"))
    OpenRouterLLM(model="m", transport=FakeTransport({"n": 1, "k": "a"})).complete_json(
        "sys", "supplier of ERCS?", SCHEMA, "probe", client="ERCS")
    logged = (tmp_path / "outbox").glob("*probe*.txt").__next__().read_text()
    assert "supplier of [CLIENT]?" in logged and "ERCS" not in logged


def test_gemini_backend_requires_paid_tier_and_speaks_gemini():
    from tendersure.llm import GeminiLLM
    from tendersure.privacy import PrivacyError
    with pytest.raises(PrivacyError):
        GeminiLLM(model="gemini-4-argon", transport=lambda *a: {}, paid_tier=False)

    def transport(url, headers, body):
        transport.seen = (url, headers, body)
        return {"modelVersion": "gemini-4-argon-001",
                "candidates": [{"finishReason": "STOP", "content": {"parts": [
                    {"text": "thinking...", "thought": True}, {"text": '{"n": 1, "k": "b"}'}]}}]}
    llm = GeminiLLM(model="google/gemini-4-argon", transport=transport, paid_tier=True)
    assert llm.complete_json("sys", "p", SCHEMA, "x") == {"n": 1, "k": "b"}
    url, headers, body = transport.seen
    assert url.endswith("/models/gemini-4-argon:generateContent")
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert llm.last_model == "gemini-4-argon-001"


def test_make_llm_picks_the_backend(monkeypatch):
    from tendersure.llm import GeminiLLM, make_llm
    monkeypatch.setenv("TENDERSURE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_PAID_TIER", "yes")
    assert isinstance(make_llm(), GeminiLLM)


def test_notice_narrative_is_not_sent():
    from tendersure.category_llm import table_only
    text = ("The Ethiopian society, one of the 192 national Red Cross and Red Crescent societies around the world, "
            "was founded on July 8th, 1935, by government Decree.\n"
            "   #   CATEGORY DESCRIPTION        SUB-CATEGORY\n"
            "              GENERAL\n"
            "   1   Supply of Stationery and Toners   Supply of office Stationery\n"
            "Should you require any assistance, kindly contact us by phone, Telegram or WhatsApp at any time.\n"
            "   2   Supply of Electronics/ Home Appliances (TVs,\n")
    out = table_only(text)
    assert "1935" not in out and "Red Cross" not in out and "assistance" not in out
    assert "Supply of Stationery and Toners" in out and "Supply of Electronics" in out


def test_evaluate_model_scores_and_resumes(corpus, tmp_path):
    from tendersure import trust
    from tendersure.evaluate import evaluate_model, model_jobs, summarise_model
    sheets = load_corpus(str(corpus))
    gen = Generator(sheets, Retriever(sheets), grades=trust.grade(sheets))
    jobs = model_jobs(gen, sheets)
    assert jobs
    t = FakeTransport({"drop": [], "reword": [], "add": [], "drafts": []})
    sel = ModelSelector(llm=OpenRouterLLM(model="m", transport=t))
    cache = str(tmp_path / "eval.json")
    s, by_client, failures = evaluate_model(jobs, sel, cache, workers=1, progress=lambda *_: None)
    assert s["judged"] == len(jobs) and not failures
    assert "FLAGGED ROWS" in summarise_model(s, by_client, failures)
    calls = len(t.calls)
    evaluate_model(model_jobs(gen, sheets), sel, cache, workers=1, progress=lambda *_: None)
    assert len(t.calls) == calls                       # resumed from the cache: no new calls



# --- rewording ------------------------------------------------------------------

def test_reword_keeps_fields_and_protected_rows_are_never_dropped(corpus):
    from tendersure.review import apply_answers, decision_items
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    flagged = [i for i, r in enumerate(p.rows) if r.kind == "question" and r.needs_reading]
    target = p.rows[flagged[0]].row
    before = (target.answer_type, target.marks, target.compliance_attach)
    trade = next(i for i, r in enumerate(p.rows) if r.kind == "question"
                 and section_key(r.row.section) == "trade_reference")
    p.rows[trade].needs_reading = True                  # pretend it was flagged
    t = FakeTransport({"reword": [{"row": flagged[0], "criteria": "Do you hold a valid food handling licence? Yes/No",
                                   "reason": "same check, catering subject"}],
                       "drop": [{"row": trade, "reason": "x"}], "add": [], "drafts": []})
    prop = ModelSelector(llm=OpenRouterLLM(model="m", transport=t)).propose(p)
    assert len(prop["reword"]) == 1 and not prop["drop"]
    assert any("trade_reference rows are never removed" in x for x in prop["ignored"])
    q = next(i for i in decision_items([p]) if i.type == "question" and i.payload)
    assert q.proposed == "reword all" and "-> Do you hold a valid food handling licence" in q.decide
    out, log, problems = apply_answers([p], {})          # blank accepts the proposal
    row = next(r.row for r in p.rows if r.kind == "question" and "food handling licence" in r.row.criteria)
    assert (row.answer_type, row.marks, row.compliance_attach) == before
    assert next(i for i in out if i.id == q.id).status.startswith("applied: reworded in 1")


def test_q_answer_grammar():
    from tendersure.review import parse_q_answer
    codes = ["X-001", "X-002", "X-003"]
    rm, rw = parse_q_answer("remove: 1; reword: 2", codes, {"X-002": "new"}, 1)
    assert rm == {"X-001"} and rw == {"X-002": "new"}
    rm, rw = parse_q_answer("reword 3: my own words", codes, {}, 1)
    assert rw == {"X-003": "my own words"}
    rm, _ = parse_q_answer("keep: 2", codes, {}, 1)
    assert rm == {"X-001", "X-003"}
    for bad in ("delete 4", "reword: 3", "remove: 9", "reword 2: x"):
        with pytest.raises(ValueError):
            parse_q_answer(bad, codes, {}, 2 if bad.startswith("reword 2") else 1)


# --- the bank as the only source -------------------------------------------------

def test_bank_ingest_is_incremental_and_complete(corpus, tmp_path):
    from tendersure.store import BankStore
    bank = BankStore(str(tmp_path / "bank.sqlite"))
    first = bank.ingest_folder(str(corpus))
    assert first["added"] == 2 and first["sheets_added"] == 4
    again = bank.ingest_folder(str(corpus))
    assert again["added"] == 0 and again["unchanged"] == 2          # nothing re-read
    fresh = {s.name: s for s in load_corpus(str(corpus), cache=False)}
    stored = {s.name: s for s in bank.load_sheets()}
    assert set(stored) == set(fresh)
    for name, s in stored.items():
        assert [r.criteria for r in s.rows] == [r.criteria for r in fresh[name].rows]
        assert [(x.label, x.marks) for x in s.structural] == [(x.label, x.marks) for x in fresh[name].structural]
    # a changed workbook replaces its earlier version
    wb = openpyxl.load_workbook(corpus / "BBB.xlsx")
    wb.active.cell(2, 2, "BBB-ET-001: Provision of Catering and Canteen Services")
    wb.save(corpus / "BBB.xlsx")
    changed = bank.ingest_folder(str(corpus))
    assert changed["replaced"] == 1 and bank.summary()["sheets"] == 4
    assert any(s.description.endswith("Canteen Services") for s in bank.load_sheets())


def test_build_needs_only_the_bank(corpus, tmp_path):
    from tendersure.store import BankStore
    bank = BankStore(str(tmp_path / "bank.sqlite"))
    bank.ingest_folder(str(corpus))
    for f in corpus.glob("*.xlsx"):
        f.unlink()                                         # the original workbooks are gone
    sheets = bank.load_sheets()
    g = Generator(sheets, Retriever(sheets))
    p = g.plan(Target("AAA-ET-2026-010", "Provision of Cleaning Services", "AAA"))
    out = tmp_path / "out.xlsx"
    Writer(template_loader=bank.template).write([p], str(out))
    back = parse_workbook(str(out))[0]
    assert len(back.rows) == len(p.questions) and validate_sheet(back) == []


def test_rewording_is_applied_during_the_build(corpus):
    from tendersure.review import auto_reword
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    i = next(i for i, r in enumerate(p.rows) if r.kind == "question" and r.needs_reading)
    original = p.rows[i].row.criteria
    t = FakeTransport({"reword": [{"row": i, "criteria": "Do you hold a food handling licence? Yes/No",
                                   "reason": "catering subject"}], "drop": [], "add": [], "drafts": []})
    ModelSelector(llm=OpenRouterLLM(model="m", transport=t)).propose(p)
    items, n = auto_reword([p])
    texts = [q.row.criteria for q in p.questions]
    assert n == 1 and "Do you hold a food handling licence? Yes/No" in texts and original not in texts
    q = next(it for it in items if it.type == "question" and it.payload)
    assert q.status == "reworded automatically" and q.answer == ""


# --- AI reviewer: only what both passes agree on is applied -----------------------

def _proposed_catering(corpus):
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    flagged = [i for i, r in enumerate(p.rows) if r.kind == "question" and r.needs_reading]
    food = next(j for j, s in enumerate(p.suggestions) if "food safety" in s.row.criteria)
    t = FakeTransport({
        "drop": [{"row": flagged[0], "reason": "not catering"}],
        "reword": [{"row": flagged[1], "criteria": "Attach evidence of trained catering staff",
                    "reason": "catering subject"}],
        "add": [{"suggestion": food, "reason": "catering needs it"}],
        "drafts": [{"section": "technical", "criteria": "Attach a food handling permit",
                    "answer_type": 5, "options": "", "reason": "licence"}]})
    ModelSelector(llm=OpenRouterLLM(model="m", transport=t)).propose(p)
    return g, p, p.rows[flagged[0]].row.criteria


def test_ai_review_applies_only_agreed_changes(corpus):
    from tendersure.reviewer import ModelReviewer, ai_review
    g, p, dropped_text = _proposed_catering(corpus)
    verdicts = FakeTransport({"verdicts": [
        {"id": "D1", "verdict": "disagree", "text": "", "reason": "still relevant"},
        {"id": "D2", "verdict": "agree", "text": "", "reason": "ok"},
        {"id": "D3", "verdict": "agree", "text": "", "reason": "ok"},
        {"id": "D4", "verdict": "edit", "text": "Attach a valid food handling permit from the health authority",
         "reason": "more precise"}]})
    items, summary = ai_review(g, [p], ModelReviewer(llm=OpenRouterLLM(model="m", transport=verdicts)),
                               progress=lambda *_: None)
    texts = [q.row.criteria for q in p.questions]
    assert dropped_text in texts                                    # removal refused: kept
    assert "Attach evidence of trained catering staff" in texts     # rewording agreed
    assert any("food safety" in x for x in texts)                   # addition agreed
    assert "Attach a valid food handling permit from the health authority" in texts   # edited draft
    assert summary["disagreements"] == 2                            # the refusal + the edit note
    assert all(i.status.startswith("AI:") for i in items if i.answer)
    check_total(p)
    sent = json.dumps(verdicts.calls[0][2])
    assert "asked in" in sent or "no past questionnaires" in sent   # reviewer got bank evidence


def test_failed_review_applies_nothing(corpus):
    from tendersure.reviewer import ModelReviewer, ai_review
    g, p, dropped_text = _proposed_catering(corpus)
    n_before = len(p.questions)

    def broken(url, headers, body):
        raise LLMError("HTTP 500: provider down")
    ai_review(g, [p], ModelReviewer(llm=OpenRouterLLM(model="m", transport=broken)), progress=lambda *_: None)
    texts = [q.row.criteria for q in p.questions]
    assert len(p.questions) == n_before and dropped_text in texts
    assert "Attach evidence of trained catering staff" not in texts and not any("food safety" in x for x in texts)


def test_remove_instead_of_reword_needs_the_bank_too(corpus):
    from tendersure.reviewer import ModelReviewer, ai_review, bank_says_absent
    assert bank_says_absent({"asked": 0, "compared": 3}) and not bank_says_absent({"asked": 0, "compared": 1})
    g, p, _ = _proposed_catering(corpus)
    reworded_from = next(q.row.criteria for q in p.questions if q.needs_reading
                         and "trained service staff" in q.row.criteria)
    verdicts = FakeTransport({"verdicts": [
        {"id": "D1", "verdict": "disagree", "text": "", "reason": "x"},
        {"id": "D2", "verdict": "remove", "text": "", "reason": "does not apply"},
        {"id": "D3", "verdict": "disagree", "text": "", "reason": "x"},
        {"id": "D4", "verdict": "disagree", "text": "", "reason": "x"}]})
    items, _ = ai_review(g, [p], ModelReviewer(llm=OpenRouterLLM(model="m", transport=verdicts)),
                         progress=lambda *_: None)
    texts = [q.row.criteria for q in p.questions]
    assert reworded_from in texts                     # only one similar past sheet: the bank can't confirm
    assert any("bank does not confirm" in i.status for i in items)


def test_ai_runs_by_default_when_configured(monkeypatch):
    from tendersure.flows import Model
    monkeypatch.setenv("TENDERSURE_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_PAID_TIER", "yes")
    assert Model().wanted()[0]
    monkeypatch.setenv("GEMINI_PAID_TIER", "no")
    assert not Model().wanted()[0]                       # unpaid key: never used automatically
    monkeypatch.setenv("GEMINI_PAID_TIER", "yes")
    assert not Model(off=True).wanted()[0]


def test_nothing_left_open_after_ai_review(corpus):
    from tendersure.reviewer import ModelReviewer, ai_review
    g, p, _ = _proposed_catering(corpus)
    verdicts = FakeTransport({"verdicts": [{"id": f"D{i}", "verdict": "agree", "text": "", "reason": "ok"}
                                           for i in range(1, 5)]})
    items, _ = ai_review(g, [p], ModelReviewer(llm=OpenRouterLLM(model="m", transport=verdicts)),
                         progress=lambda *_: None)
    assert items and all(i.status for i in items)            # every line settled, with a reason
    assert any(i.status.startswith("AI: kept the client's marks") for i in items if i.type == "marks")


def test_removal_that_strands_marks_is_rebalanced(corpus):
    """The crash seen in the first automatic run: a scored question removed from a
    section whose other questions carry no marks."""
    from tendersure.reviewer import ModelReviewer, ai_review
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    tech = [i for i, r in enumerate(p.rows) if r.kind == "question" and r.needs_reading]
    a, b = p.rows[tech[0]].row, p.rows[tech[1]].row
    a.marks, a.system, a.compliance = 20.0, 20.0, None       # all the section's marks on one question
    b.marks, b.system, b.compliance, b.compliance_attach = 0.0, 0.0, None, False
    check_total(p)
    t = FakeTransport({"drop": [{"row": tech[0], "reason": "not catering"}], "reword": [], "add": [], "drafts": []})
    ModelSelector(llm=OpenRouterLLM(model="m", transport=t)).propose(p)
    verdicts = FakeTransport({"verdicts": [{"id": "D1", "verdict": "agree", "text": "", "reason": "ok"}]})
    items, summary = ai_review(g, [p], ModelReviewer(llm=OpenRouterLLM(model="m", transport=verdicts)),
                               progress=lambda *_: None)
    check_total(p)                                           # back at 100
    assert summary["rebalanced"] == ["AAA-ET-2026-031"]
    assert any("marks rebalanced to 100" in n for n in p.notes)
    assert any(i.status.startswith("AI: marks rebalanced") for i in items if i.type == "marks")


def test_protected_rows_survive_every_removal_path(corpus, monkeypatch):
    """Trade reference / declaration / submission rows must never be removed by the
    AI - including the reviewer's 'remove instead of reword' path."""
    import tendersure.reviewer as rv
    from tendersure.reviewer import ModelReviewer, ai_review
    monkeypatch.setattr(rv, "removal_evidence", lambda gen, plan, text: ("asked in 0 of 3", 0, 3))
    _, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
    i = next(i for i, r in enumerate(p.rows) if r.kind == "question"
             and section_key(r.row.section) == "trade_reference" and (r.row.marks or 0) > 0)
    p.rows[i].needs_reading = True
    protected_text = p.rows[i].row.criteria
    t = FakeTransport({"drop": [{"row": i, "reason": "x"}],
                       "reword": [{"row": i, "criteria": "Attach evidence of catering contracts", "reason": "x"}],
                       "add": [], "drafts": []})
    ModelSelector(llm=OpenRouterLLM(model="m", transport=t)).propose(p)
    verdicts = FakeTransport({"verdicts": [{"id": "D1", "verdict": "remove", "text": "", "reason": "x"}]})
    ai_review(g, [p], ModelReviewer(llm=OpenRouterLLM(model="m", transport=verdicts)), progress=lambda *_: None)
    texts = [q.row.criteria for q in p.questions]
    assert protected_text in texts or "Attach evidence of catering contracts" in texts
    check_total(p)


def test_apply_layer_refuses_ai_removal_of_protected_rows(corpus):
    from tendersure.review import apply_answers, decision_items
    from tendersure.schema import PROTECTED_SECTIONS

    def plan_with_flagged_trade_row():
        _, g = _gen(corpus)
        p = g.plan(Target("AAA-ET-2026-031", "Provision of Catering Services", "AAA"))
        i = next(i for i, r in enumerate(p.rows) if r.kind == "question"
                 and section_key(r.row.section) == "trade_reference" and (r.row.marks or 0) > 0)
        p.rows[i].needs_reading = True
        return p, p.rows[i].row.criteria

    p, text = plan_with_flagged_trade_row()
    q = next(i for i in decision_items([p]) if i.type == "question" and "trade_reference" in i.decide)
    out, _, problems = apply_answers([p], {q.id: "remove all"}, protect=PROTECTED_SECTIONS)
    assert text in [x.row.criteria for x in p.questions]                 # AI route: refused
    assert next(i for i in out if i.id == q.id).status == "removal refused: protected section"
    p2, text2 = plan_with_flagged_trade_row()
    apply_answers([p2], {q.id: "remove all"})                            # a person's explicit answer
    assert text2 not in [x.row.criteria for x in p2.questions]


# --- glossary: short names and acronyms ------------------------------------------

def _gl_sheet(name, client, rows):
    from tendersure.parser import Row, Sheet
    s = Sheet("f.xlsx", name, name, name, "d", client, 4, 14)
    s.rows = [Row("f.xlsx", name, 5 + i, criteria=c, answer_type=5, compliance_attach=True,
                  archive_short_name=sn, acronym=ac) for i, (c, sn, ac) in enumerate(rows)]
    return s


def test_glossary_seeds_client_first_with_a_cross_client_fallback():
    from tendersure import glossary as gl
    ref = "Attach evidence e.g. PO or award letter"
    sheets = [_gl_sheet("AAA-1", "AAA", [("Attach TIN certificate", "TIN Certificate", "TIN"),
                                         (ref, "Reference 1", "REF 1"), (ref, "Reference 2", "REF 2"),
                                         ("Attach ISO 9001 certificate", "ISO 9001", "ISO")]),
              _gl_sheet("BBB-1", "BBB", [("Attach TIN certificate", "TIN Certificate", "TIN/PIN"),
                                         ("Do you have a safety policy?", None, None),
                                         ("If yes attach a copy", "SHE Policy", "SHE")]),
              _gl_sheet("BBB-2", "BBB", [("Attach TIN certificate", "TIN Certificate", "TIN/PIN")])]
    g = gl.Glossary(gl.seed(sheets))
    assert g.lookup("AAA", "attach tin certificate").names() == ("TIN Certificate", "TIN")
    assert g.lookup("BBB", "attach tin certificate").names() == ("TIN Certificate", "TIN/PIN")
    assert g.lookup("CCC", "attach tin certificate").names() == ("TIN Certificate", "TIN/PIN")  # 2 of 3 uses
    e = g.lookup("AAA", "attach evidence eg po or award letter")
    assert e.numbered and e.names(3) == ("Reference 3", "REF 3")
    assert g.lookup("AAA", "attach iso 9001 certificate").names() == ("ISO 9001", "ISO")      # not item 9001
    assert g.lookup("BBB", "do you have a safety policy >> if yes attach a copy").names() == ("SHE Policy", "SHE")
    assert g.lookup("BBB", "if yes attach a copy") is None                                  # only in context


def test_glossary_reseed_keeps_confirmed_and_drafted_rows(tmp_path):
    from tendersure import glossary as gl
    old = [gl.Entry("AAA", "attach tin certificate", "x", "Tax ID", "TID", status=gl.CONFIRMED),
           gl.Entry("AAA", "attach office lease", "x", "Lease", "LSE", status=gl.DRAFTED),
           gl.Entry("AAA", "attach bank letter", "x", "Old", "OLD", status=gl.SEEDED)]
    fresh = [gl.Entry("AAA", "attach tin certificate", "x", "TIN Certificate", "TIN", uses=3),
             gl.Entry("AAA", "attach bank letter", "x", "Bank Letter", "BL", uses=2)]
    merged = gl.reseed(old, fresh)
    gl.save(merged, str(tmp_path / "g.csv"))
    g = gl.Glossary(gl.load(str(tmp_path / "g.csv")))
    assert g.lookup("AAA", "attach tin certificate").names() == ("Tax ID", "TID")      # team's edit wins
    assert g.lookup("AAA", "attach office lease").status == gl.DRAFTED
    assert g.lookup("AAA", "attach bank letter").names() == ("Bank Letter", "BL")      # seeded row refreshed


def _named_plan(corpus, shareholders=2):
    """A finalised plan whose ID upload repeats once per shareholder."""
    from tendersure.generate import finalise, insert_rows
    from tendersure.parser import Row
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    finalise(p)
    ids = [Row("", "", 0, criteria="Attach National ID or passport", answer_type=5, attachment=True,
               archive_short_name="Shareholder ID or passport") for _ in range(shareholders)]
    insert_rows(p, "registration", ids, "reused unchanged", "test", "test")
    return p


def test_names_come_from_the_glossary_and_repeats_are_numbered(corpus):
    from tendersure import glossary as gl, naming
    from tendersure.generate import to_sheet
    from tendersure.validate import check_names
    p = _named_plan(corpus)
    g = gl.Glossary([gl.Entry("AAA", "attach evidence of physically registered office", "x", "Office Evidence", "OE"),
                     gl.Entry(gl.ANY_CLIENT, "attach evidence of trained service staff", "x", "Service Staff", "SS")])
    drafted = naming.name_documents([p], g, naming.RuleDrafter())
    rows = {q.row.criteria: q for q in p.questions}

    office = rows["Attach evidence of physically registered office"]
    assert (office.row.archive_short_name, office.row.acronym) == ("Office", "OE")    # client's own name kept
    assert any("differs from the glossary's 'Office Evidence'" in n for n in office.notes)
    assert rows["Attach evidence of trained service staff"].row.acronym == "SS"       # cross-client entry
    ref = rows["Attach evidence e.g. PO or award letter"].row
    assert (ref.archive_short_name, ref.acronym) == ("Reference 1", "REF 1")         # its own number kept

    ids = [q.row for q in p.questions if q.row.criteria == "Attach National ID or passport"]
    assert [(r.archive_short_name, r.acronym) for r in ids] == \
           [("Shareholder ID or passport 1", "SIDP 1"), ("Shareholder ID or passport 2", "SIDP 2")]
    assert [e.status for e in drafted] == [gl.DRAFTED]                                # one question, named once
    assert check_names(to_sheet(p)) == []


def test_model_drafts_short_names_and_the_rule_builds_acronyms(corpus):
    from tendersure import glossary as gl, naming
    from tendersure.generate import insert_rows
    from tendersure.parser import Row
    p = _named_plan(corpus, shareholders=1)
    unnamed = [Row("", "", 0, criteria=c, answer_type=5, compliance_attach=True) for c in
               ("Please attach evidence of ownership or rent agreement for welding machine",
                "Please attach evidence of ownership or rent agreement for a concrete mixer")]
    insert_rows(p, "technical", unnamed, "newly drafted", "model draft", "test")
    t = FakeTransport({"names": [{"id": 0, "short_name": "Welding Machine Ownership"},
                                 {"id": 1, "short_name": "Proof that the company owns a concrete mixer"}]})
    drafter = naming.ModelDrafter(llm=OpenRouterLLM(model="m", transport=t))
    g = gl.Glossary([gl.Entry("AAA", "please attach evidence of ownership or rent agreement for welding machine",
                              "x", "Ownership or Rent Agreement", "ORA", status=gl.RULE_DRAFTED)])  # redone
    drafted = {e.example: e for e in naming.name_documents([p], g, drafter)}

    assert len(t.calls) == 1                         # rows with a Short Name never reach the model
    sent = json.dumps(t.calls[0][2])
    assert "welding machine" in sent and "Shareholder ID" not in sent
    weld = drafted["Please attach evidence of ownership or rent agreement for welding machine"]
    assert (weld.short_name, weld.acronym, weld.status) == ("Welding Machine Ownership", "WMO", gl.DRAFTED)
    mixer = drafted["Please attach evidence of ownership or rent agreement for a concrete mixer"]
    assert (mixer.short_name, mixer.acronym, mixer.status) == ("Ownership or Rent Agreement", "ORA",
                                                              gl.RULE_DRAFTED)   # 8 words: refused, rule draft
    office = drafted["Attach evidence of physically registered office"]
    assert (office.short_name, office.acronym) == ("Office", "OFF")


@pytest.mark.parametrize("name,acronym", [
    ("TIN Certificate", "TIN"), ("Tax Compliance Certificate", "TCC"), ("SHE Policy", "SHE"),
    ("Structural Engineer CV", "SECV"), ("Accounts", "ACC"), ("Reference", "REF"),
    ("Bank account request details form", "BARDF"), ("Shareholder ID or passport", "SIDP"),
    ("Tax Compliance/Clearance Certificate", "TCC"), ("EPA License", "EPA")])
def test_acronyms_combine_the_short_names_letters(name, acronym):
    from tendersure.naming import acronym_for
    assert acronym_for(name) == acronym


def test_acronyms_stay_unique_within_a_sheet(corpus):
    from tendersure import glossary as gl, naming
    p = _named_plan(corpus, shareholders=0)
    g = gl.Glossary([gl.Entry("AAA", "attach evidence of physically registered office", "x", "Office", "TIN"),
                     gl.Entry("AAA", "attach evidence of trained service staff", "x", "Staff", "STF")])
    naming.name_documents([p], g, naming.RuleDrafter())
    acronyms = [q.row.acronym for q in p.questions if q.row.acronym]
    assert len(acronyms) == len(set(acronyms)) and "TIN2" in acronyms


def test_glossary_fill_rebuilds_drafts_and_keeps_one_acronym_per_document(tmp_path):
    from tendersure import glossary as gl, naming
    E = gl.Entry
    entries = [E("ERCS", "attach id", "x", "Shareholder ID or passport", "SID", status=gl.DRAFTED, uses=24),
               E("ILRI", "attach id", "x", "Shareholder ID or passport", "", uses=38),
               E(gl.ANY_CLIENT, "attach id", "x", "Shareholder ID or passport", "", uses=70),
               E("ILRI", "attach staff 1", "x", "Staff", "", numbered=True, uses=9),
               E("ILRI", "attach staff 2", "x", "Staff 2", "", uses=9),
               E(gl.ANY_CLIENT, "attach iso", "x", "ISO 9001", "", uses=2),
               E("ERCS", "attach coi", "x", "Company Registration Certificate", "COI", uses=12),     # client's own
               E("ERCS", "attach weld", "x", "Welding Machine Ownership", "WELD", status=gl.DRAFTED)]

    class NoModel(naming.RuleDrafter):
        def draft(self, needs):
            assert needs == []                       # every entry has a Short Name: nothing to draft
            return []

    rederived, reused, built = naming.complete_glossary(gl.Glossary(entries), NoModel())
    got = {(e.client, e.short_name): e.acronym for e in entries}
    assert got[("ERCS", "Shareholder ID or passport")] == "SIDP"          # draft rebuilt by the rule
    assert got[("ILRI", "Shareholder ID or passport")] == got[("*", "Shareholder ID or passport")] == "SIDP"
    assert (got[("ILRI", "Staff")], got[("ILRI", "Staff 2")]) == ("STA", "STA 2")
    assert got[("*", "ISO 9001")] == "ISO9001"                            # not item 9001 of 'ISO'
    assert got[("ERCS", "Company Registration Certificate")] == "COI"     # the client's acronym kept
    assert got[("ERCS", "Welding Machine Ownership")] == "WMO"
    assert (rederived, reused, built) == (2, 2, 3)

    gl.save(entries, str(tmp_path / "g.csv"))
    order = [(e.acronym, e.client) for e in gl.load(str(tmp_path / "g.csv"))]
    assert order[:4] == [("COI", "ERCS"), ("ISO9001", "*"), ("SIDP", "ERCS"), ("SIDP", "ILRI")]


@pytest.mark.parametrize("marks,scores,fixed", [
    (0, "1,2,3", "0,0,0"),          # NRC 'Lead time': 0 marks, options scored
    (3, "0,2", "0,3"),              # ILRI 'declared in default': best option short of the marks
    (2, "0,1,3", "0,1,2"),          # best option over the marks: scaled down
    (5, "0,5", None),               # already right
    (4, "0,0", None),               # no option is known to be best: left for a person
])
def test_best_option_is_scaled_to_the_rows_marks(marks, scores, fixed):
    from tendersure.parser import Row
    from tendersure.validate import fix_option_scores
    r = Row("", "", 0, criteria="q", answer_type=2, marks=marks, options="a,b,c"[:len(scores) * 2 - 1],
            option_scores=scores)
    note = fix_option_scores(r)
    assert r.option_scores == (fixed or scores) and bool(note) == bool(fixed)


# --- yes/no questions -------------------------------------------------------------

YN = {"recycle": "Does your company recycle waste responsibly? Yes/No",
      "default": "Has the firm ever been declared in default of a contract?",
      "supplier": "Is your company an existing supplier? Yes/No",
      "licence": "Do you hold a valid service licence? Yes/No"}


def _yn_plan(corpus):
    """The synthetic plan (its 'licence' question is type 4 worth 10) plus a
    positive question scored Yes=0, a negative one as a toggle with compliance
    marks, and an unscored Yes/No select."""
    from tendersure.generate import PlannedRow, finalise
    from tendersure.parser import Row
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    finalise(p)
    extra = [Row("", "", 0, section="Business Experience- EXP", criteria=YN["recycle"], answer_type=2, marks=2,
                 system=2, options="Yes, No", option_scores="0,2"),
             Row("", "", 0, section="Business Experience- EXP", criteria=YN["default"], answer_type=4, marks=3,
                 compliance=3, compliance_attach=True),
             Row("", "", 0, section="Business Experience- EXP", criteria=YN["supplier"], answer_type=2, marks=0,
                 system=0, options="Yes,No", option_scores="0,0")]
    at = next(i for i, q in enumerate(p.rows) if q.kind == "total")
    p.rows[at:at] = [PlannedRow(kind="question", row=r) for r in extra]
    return p


def _rows(p):
    return {k: next(q.row for q in p.questions if q.row.criteria == t) for k, t in YN.items()}


def test_yes_no_rule_sets_the_answer_type_from_whether_yes_is_good(corpus):
    from tendersure import yesno
    from tendersure.generate import to_sheet
    from tendersure.trust import context_key
    from tendersure.validate import check_yes_no
    p = _yn_plan(corpus)
    book = yesno.Book([])
    judged = yesno.settle([p], book, yesno.RuleJudge())
    r = _rows(p)
    assert (r["recycle"].answer_type, r["recycle"].options, r["recycle"].option_scores) == (4, None, None)
    assert (r["default"].answer_type, r["default"].options, r["default"].option_scores) == (2, "Yes,No", "0,3")
    assert (r["default"].compliance, r["default"].system) == (3, None)          # marks stay where they were
    assert (r["supplier"].answer_type, r["supplier"].options) == (4, None)      # no marks: a toggle
    assert r["licence"].answer_type == 4
    assert sorted((v.example, v.yes_is) for v in judged) == sorted(
        [(YN["recycle"], "good"), (YN["default"], "bad"), (YN["licence"], "good")])   # only scored ones

    def yes_is(rows, i):
        v = book.lookup(context_key(rows, i))
        return v.yes_is if v else None
    assert check_yes_no(to_sheet(p), yes_is) == []


def test_model_judges_unconfirmed_scored_questions_in_one_call(corpus, tmp_path):
    from tendersure import yesno
    p = _yn_plan(corpus)
    book = yesno.Book([yesno.Verdict("do you hold a valid service licence yes/no", "x", "good", yesno.CONFIRMED),
                       yesno.Verdict("has the firm ever been declared in default of a contract", "x", "good",
                                     yesno.RULE_DRAFTED)])                         # a keyword draft: redone
    t = FakeTransport({"answers": [{"id": 0, "yes_is": "good"}]})                  # nothing for id 1
    judged = {v.example: v for v in yesno.settle([p], book, yesno.ModelJudge(llm=OpenRouterLLM(model="m", transport=t)))}

    sent = json.dumps(t.calls[0][2])
    assert len(t.calls) == 1 and "recycle" in sent and "declared in default" in sent
    assert "service licence" not in sent and "existing supplier" not in sent   # confirmed / unscored
    assert (judged[YN["recycle"]].yes_is, judged[YN["recycle"]].status) == ("good", yesno.DRAFTED)
    assert (judged[YN["default"]].yes_is, judged[YN["default"]].status) == ("bad", yesno.RULE_DRAFTED)
    assert _rows(p)["default"].options == "Yes,No"

    yesno.save(book, str(tmp_path / "yn.csv"))
    again = yesno.load(str(tmp_path / "yn.csv"))
    assert again.lookup("do you hold a valid service licence yes/no").status == yesno.CONFIRMED
    assert len(again.verdicts) == 3


# --- learning from the bank ----------------------------------------------------------

def test_rewordings_group_and_confirmed_questions_stay_apart():
    from tendersure import grouping
    keys = ["does your company have the necessary technology and equipment to manage your operations efficiently",
            "does your organization have the necessary technology and equipment to manage your operations efficiently",
            "do you offer extended warranty on goods sold yes/no",
            "do you offer extended warranty on goods sold",
            "has the firm ever been declared in default of a contract"]
    g = grouping.group(keys)
    assert g[keys[1]] == keys[0] and g[keys[3]] == keys[2]
    assert len(set(g.values())) == 3
    confirmed = {keys[2], keys[3]}
    g = grouping.group(keys, apart=lambda a, b: a in confirmed and b in confirmed)
    assert g[keys[3]] == keys[3]                          # the team confirmed both: never merged
    cv = "attach evidencecv contract and education experience for {} with atleast 4 years experience"
    g = grouping.group([cv.format("structural engineer"), cv.format("architect"), cv.format("mep engineer"),
                        cv.format("electrical engineer"), cv.format("mechanical engineer")])
    assert len(set(g.values())) == 5                      # one template, five different documents


def test_a_rewording_takes_its_confirmed_twins_answers_without_a_model():
    from tendersure import glossary as gl, naming, yesno
    twin = "does your company have the necessary technology and equipment to manage your operations efficiently"
    new = "does your organization have the necessary technology and equipment to manage your operations efficiently"
    book = yesno.Book([yesno.Verdict(twin, "x", "good", yesno.CONFIRMED)])
    reused, rest = yesno.reuse_similar([yesno.Need("AAA", new, "Does your organization ...")], book)
    assert rest == [] and (reused[0].yes_is, reused[0].status) == ("good", yesno.SIMILAR)

    doc = "please attach evidence of ownership or rent agreement for welding machine"
    g = gl.Glossary([gl.Entry("AAA", doc, "x", "Welding Machine Ownership", "WMO", status=gl.CONFIRMED)])
    reused, rest = naming.reuse_similar([naming.Need("BBB", "please attach evidence of ownership or rent "
                                                           "agreement for the welding machine", "...")], g)
    assert rest == [] and (reused[0].short_name, reused[0].acronym, reused[0].status) == \
           ("Welding Machine Ownership", "WMO", gl.SIMILAR)
    _, rest = naming.reuse_similar([naming.Need("BBB", "attach a fire safety certificate", "...")], g)
    assert len(rest) == 1                                  # nothing similar: still needs drafting


def test_model_calls_go_in_chunks_and_a_failed_chunk_falls_back_alone():
    from tendersure import yesno
    from tendersure.llm import LLMError

    class Transport:
        calls = 0

        def __call__(self, url, headers, body):
            Transport.calls += 1
            if "FAIL" in json.dumps(body):
                raise LLMError("provider down")
            return {"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
                {"answers": [{"id": 0, "yes_is": "bad"}, {"id": 1, "yes_is": "bad"}]})}}]}

    judge = yesno.ModelJudge(llm=OpenRouterLLM(model="m", transport=Transport()))
    judge.chunk = 2
    needs = [yesno.Need("AAA", f"q{i}", text) for i, text in
             enumerate(["Do you recycle? A", "Do you recycle? B", "Do you recycle? FAIL", "Do you recycle? D",
                        "Do you recycle? E"])]
    got = judge.judge(needs)
    assert Transport.calls == 3
    assert [(v.yes_is, v.status) for v in got] == [("bad", yesno.DRAFTED)] * 2 + \
           [("good", yesno.RULE_DRAFTED)] * 2 + [("bad", yesno.DRAFTED)]


def test_learn_names_and_judges_the_history_and_applies_the_review(corpus, tmp_path, caplog):
    from tendersure import flows, glossary as gl, yesno
    caplog.set_level("INFO", logger="tendersure")
    store, review = tmp_path / "learned", tmp_path / "review.xlsx"
    flows.learn(str(store), str(review), source=str(corpus), model=flows.Model(off=True))

    entries = gl.load(str(store / "glossary.csv"))
    assert entries and all(e.short_name and e.acronym for e in entries)
    food = next(e for e in entries if e.question_key.startswith("please attach your food safety"))
    assert (food.short_name, food.acronym, food.status) == ("Food Safety and Hygiene", "FSH", gl.RULE_DRAFTED)
    book = yesno.load(str(store / "yesno_polarity.csv"))
    assert {v.question_key for v in book.verdicts} == {
        "do you offer extended warranty on goods sold yes/no", "do you hold a valid service licence yes/no",
        "do you use temperature regulated food containers yes/no"}           # the scored ones only

    wb = openpyxl.load_workbook(review)
    names, yn = wb["Names"], wb["Yes-No"]
    row_of = {names.cell(r, 2).value: r for r in range(2, names.max_row + 1)}
    names.cell(row_of["Please attach your food safety and hygiene procedures"], 8, "Food Safety Procedures | FSP")
    names.cell(row_of["Attach evidence of trained service staff"], 8, "ok")
    yn.cell(2, 6, "bad")
    wb.save(review)
    applied = flows.apply_learned_review(str(review), str(store), str(tmp_path / "r2.xlsx")).applied

    food = next(e for e in gl.load(str(store / "glossary.csv")) if e.question_key == food.question_key)
    assert (food.short_name, food.acronym, food.status) == ("Food Safety Procedures", "FSP", gl.CONFIRMED)
    assert sum(v.status == yesno.CONFIRMED for v in yesno.load(str(store / "yesno_polarity.csv")).verdicts) == 1
    assert applied == 3
    caplog.clear()

    flows.learn(str(store), str(tmp_path / "r3.xlsx"), source=str(corpus), model=flows.Model(off=True))
    out = caplog.text
    assert "0 questions newly named" in out and "0 questions judged" in out     # nothing new: nothing asked


def test_rewordings_drafted_in_one_run_share_one_name():
    from tendersure import glossary as gl, naming
    E = gl.Entry
    a = "please attach company profile/ individual profileincluding client and project portfolios"
    b = "please attach company profile/ individual profile including client and project portfolios"
    entries = [E("SNV", a, "x", "Company Or Individual Profile", "CIP", status=gl.DRAFTED, uses=4),
               E("SNV", b, "x", "Company Profile And Portfolio", "CPP", status=gl.DRAFTED, uses=1),
               E("ERCS", b, "x", "Company Profile", "CP", status=gl.SEEDED, uses=12)]      # the client's own: kept
    assert naming.harmonise_drafts(gl.Glossary(entries)) == 1
    assert [(e.short_name, e.acronym) for e in entries] == [
        ("Company Or Individual Profile", "CIP"), ("Company Or Individual Profile", "CIP"), ("Company Profile", "CP")]


# --- category refinement -----------------------------------------------------------

def _draft_book(path, rows, header=("No", "Category Description")):
    """A draft list laid out like the clients': numbers in B, text in C, group rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["B3"], ws["C3"] = header
    for i, (num, text) in enumerate(rows, 4):
        ws.cell(i, 2, num)
        ws.cell(i, 3, text)
    wb.save(path)
    return str(path)


RC = [(None, "GENERAL"),
      (1, "Supply of office stationery"),
      (2, "Provision of Translation and Interpretation services"),
      (3, "Provision of travel agency services (IATA Registered Only)"),
      ("ICT", None),                                          # a group label in the number column
      (4, "Supply, installation and maintenance of ICT equipment (Servers, Routers)"),
      (5, "Supply and Maintenance of ICT Equipment (Computers, Laptops)"),
      (6, "Provision of repairs and maintenance of fire fighting eqiupment"),
      (7, "Provision of repairs and maintenance of fire fighting eqiupment")]


def test_draft_lists_are_read_in_both_client_layouts(tmp_path):
    from tendersure.refine import read_draft
    rc = read_draft(_draft_book(tmp_path / "rc.xlsx", RC))
    assert [(d.id, d.group) for d in rc][:2] == [("1", "GENERAL"), ("2", "GENERAL")]
    assert [d.group for d in rc][3:] == ["ICT"] * 4
    nrc = read_draft(_draft_book(tmp_path / "nrc.xlsx", [
        ("NRC/2026-27/001", "Supply of ICT Equipment"), ("NRC/2026-27/023", "Supply of Stationery"),
        ("Category B", None), ("Reference", "Prequalification for Provision of Services"),
        ("NRC/2026 27/006", "Provision of household movers' services")],
        header=("Reference", "Prequalification for Supply of Goods")))
    assert [(d.id, d.group) for d in nrc] == [("1", "Prequalification for Supply of Goods"),
                                              ("23", "Prequalification for Supply of Goods"),
                                              ("6", "Prequalification for Provision of Services")]


def _requirements_book(path):
    """A draft with the optional columns (spec 07): sub-categories on rows
    under their category, licence / HR / technical cells, and an Individual
    Consultant note in a column with no header."""
    wb = openpyxl.Workbook()
    ws = wb.active
    for col, label in zip("BCDEFGH", ["Category #", "#", "Category Description", "SUB-CATEGORY",
                                     "Contractor Level", "HR", "Technical Specification"]):
        ws[f"{col}5"] = label
    rows = [{"D": "GENERAL"},
            {"C": 1, "D": "Supply of relief items", "E": "Soaps"}, {"E": "Blankets"}, {"E": "Buckets"},
            {"D": "CONSULTANCY"},
            {"C": 2, "D": "Provision of consultancy services", "E": "Data Collection",
             "I": "Includes Individual Consultant"},
            {"E": "Data Analysis"},
            {"C": 3, "D": "Provision of design consultancy for buildings", "E": "Consultancy certification ",
             "G": "Civil/ Hydraulic and related minimum 5 years, Economics"},
            {"D": "ENGINEERING WORKS and MATERIALS"},
            {"C": 4, "D": "Provision of minor construction works", "F": "min G5/ BC5 or above",
             "G": "Project Manager, Site engineer/Residential Engineer, Foreman",
             "H": "(Dump Truck, Excavator/back loader, pick up"},
            {"C": 5, "D": "Supply and installation of solar equipment",
             "H": "MAF/DAF, Evidence letter for min 1 year Warranty"}]
    for r, cells in enumerate(rows, 6):
        for col, v in cells.items():
            ws[f"{col}{r}"] = v
    wb.save(path)
    return str(path)


def test_a_draft_s_requirement_columns_are_read(tmp_path):
    from tendersure.refine import read_draft
    d = {x.id: x for x in read_draft(_requirements_book(tmp_path / "req.xlsx"))}
    assert list(d) == ["1", "2", "3", "4", "5"]
    assert d["1"].sub_categories == ["Soaps", "Blankets", "Buckets"]
    assert (d["2"].sub_categories, d["2"].individual) == (["Data Collection", "Data Analysis"], True)
    assert not d["1"].individual
    assert d["3"].sub_categories == [] and d["3"].licence == ["Consultancy certification"]
    assert "sub-category column" in d["3"].notes[0]
    assert d["3"].hr == ["Civil/Hydraulic and related minimum 5 years", "Economics"]
    assert d["4"].licence == ["min G5/BC5 or above"]
    assert d["4"].hr == ["Project Manager", "Site engineer/Residential Engineer", "Foreman"]
    assert d["4"].technical == ["(Dump Truck, Excavator/back loader, pick up)"]
    assert d["5"].technical == ["MAF/DAF", "Evidence letter for min 1 year Warranty"]
    assert d["4"].group == "ENGINEERING WORKS and MATERIALS"        # small words may be lower case


@pytest.mark.parametrize("text,items", [
    ("Project Manager, Site engineer, 5 years", ["Project Manager", "Site engineer 5 years"]),
    ("A; B\nC", ["A", "B", "C"]),
    ("Mixer, (Pick up, Dump Truck), Pump", ["Mixer", "(Pick up, Dump Truck)", "Pump"]),
    ("", []),
])
def test_requirement_cells_split_into_items(text, items):
    from tendersure.refine import split_items
    assert split_items(text) == items


def test_requirement_columns_survive_refine_apply_and_the_categories_table(tmp_path):
    from tendersure import categories as cat, flows
    revised, table = tmp_path / "revised.xlsx", tmp_path / "cats.csv"
    flows.propose_file(_requirements_book(tmp_path / "req.xlsx"), "AAA", out=str(revised),
                       bank=str(tmp_path / "none"), research=str(tmp_path / "r.csv"), model=flows.Model(off=True))
    wb = openpyxl.load_workbook(revised)
    ws = wb["Revised"]
    col = {c.value: c.column for c in ws[1]}
    rows = {str(ws.cell(r, col["From draft #"]).value): r for r in range(2, ws.max_row + 1)
            if ws.cell(r, col["Status"]).value}
    assert "sub-category column" in ws.cell(rows["3"], col["Question for client"]).value
    ws.cell(rows["5"], col["Decision"], "reject")                       # back to the draft row: keeps its columns
    ws.cell(rows["4"], col["HR"], "Project Manager | Foreman")           # the team corrects a cell
    wb.save(revised)
    flows.apply_file(str(revised), "AAA", str(tmp_path / "final.xlsx"), str(table), model=flows.Model(off=True))
    cats = {c.description: c for c in cat.load(str(table))}
    works = cats["Provision of Minor Construction Works"]
    assert (works.licence, works.hr) == (["min G5/BC5 or above"], ["Project Manager", "Foreman"])
    assert works.technical == ["(Dump Truck, Excavator/back loader, pick up)"]
    assert cats["Supply and Installation of Solar Equipment"].technical == [
        "MAF/DAF", "Evidence letter for min 1 year Warranty"]
    consult = cats["Provision of Consultancy Services"]
    assert consult.gate_question and "Individual Consultant" in consult.applies_to
    assert consult.sub_categories == ["Data Collection", "Data Analysis"]
    assert cats["Supply of Relief Items"].sub_categories == ["Soaps", "Blankets", "Buckets"]
    assert cat.load(str(table)) == list(cats.values())                   # the CSV round trip is lossless


@pytest.mark.parametrize("text,title", [
    ("Supply of office stationery and genuine toners & cartridges",
     "Supply of Office Stationery and Genuine Toners & Cartridges"),
    ("Provision of disposal services (e-waste, incineration etc.)",
     "Provision of Disposal Services (E-Waste, Incineration etc.)"),
    ("Supply of water, sanitation and hygiene (WASH) equipment", "Supply of Water, Sanitation and Hygiene (WASH) Equipment"),
])
def test_title_case_keeps_capitals_and_small_words(text, title):
    from tendersure.refine import title_case
    assert title_case(text) == title


def test_rules_fold_duplicates_raise_overlaps_and_fill_the_columns(tmp_path):
    from tendersure import refine as rf
    drafts = rf.read_draft(_draft_book(tmp_path / "rc.xlsx", RC))
    lines = {ln.from_draft[0]: ln for ln in rf.check(None, drafts, rf.groups_of(drafts))}
    assert (lines["7"].status, lines["7"].reason) == (rf.REPEATED, "same as #6")
    assert "Overlaps with #4" in lines["5"].question                     # ICT equipment twice
    assert lines["2"].gate_question and "Individual Consultant" in lines["2"].applies_to
    assert not lines["1"].gate_question and lines["1"].applies_to == rf.COMPANIES
    assert lines["3"].eligibility == ["IATA Registration Certificate"]
    assert (lines["1"].status, lines["1"].description) == (rf.RENAMED, "Supply of Office Stationery")


def test_model_proposals_are_checked_before_they_are_kept(tmp_path):
    from tendersure import refine as rf
    drafts = rf.read_draft(_draft_book(tmp_path / "rc.xlsx", RC))
    raw = {"lines": [
        {"from": ["4", "5"], "status": "Merged", "description": "Supply of ICT Equipment"},          # no reason
        {"from": ["6"], "status": "Renamed", "description": "Provision of Repairs and Maintenance of Fire "
                                                            "Fighting Equipment", "reason": "spelling"},
        {"from": ["7"], "status": "Repeated", "description": "x"},
        {"from": ["1"], "status": "Repeated", "description": "x"},                                  # not a duplicate
        {"from": ["2"], "status": "Separated", "description": "Provision of Translation Services", "reason": "two trades"},
        {"from": ["2"], "status": "Separated", "description": "Provision of Interpretation Services", "reason": "two trades",
         "applies_to": ["Individual Consultant", "Share Company"]}],
        "additional": [{"description": "Supply of office stationery", "reason": "dup", "group": "GENERAL"},
                       {"description": "Provision of Cleaning Services", "reason": "common", "group": "general"}]}
    lines = rf.check(raw, drafts, rf.groups_of(drafts))
    by = lambda i: [ln for ln in lines if i in ln.from_draft]
    assert [ln.status for ln in by("4")] == [rf.RENAMED] and "Merge with #5" in by("4")[0].question
    assert by("6")[0].reason == "spelling; was: Provision of repairs and maintenance of fire fighting eqiupment"
    assert by("1")[0].status == rf.RENAMED and "duplicate" in by("1")[0].question
    assert [ln.description for ln in by("2")] == ["Provision of Translation Services",
                                                   "Provision of Interpretation Services"]
    assert by("2")[1].gate_question and by("2")[1].applies_to == ["Share Company", "Individual Consultant"]
    assert by("3")[0].question == "(not reviewed by the model)"                   # skipped: kept as is
    added = [ln for ln in lines if ln.status == rf.ADDITIONAL]
    assert [(ln.description, ln.group) for ln in added] == [("Provision of Cleaning Services", "GENERAL")]
    assert rf.traceability(lines, drafts) == []


def test_refine_review_loop_ends_in_a_categories_table(tmp_path):
    from tendersure import categories as cat, flows, refine as rf
    draft = _draft_book(tmp_path / "rc.xlsx", RC)
    revised = tmp_path / "revised.xlsx"
    flows.propose_file(draft, "AAA", out=str(revised), bank=str(tmp_path / "none"), model=flows.Model(off=True))
    wb = openpyxl.load_workbook(revised)
    ws = wb["Revised"]
    col = {c.value: c.column for c in ws[1]}
    rows = {str(ws.cell(r, col["From draft #"]).value): r for r in range(2, ws.max_row + 1)
            if ws.cell(r, col["Status"]).value}
    ws.cell(rows["1"], col["Decision"], "edit")
    ws.cell(rows["1"], col["Client comment"], "Supply of Stationery and Toners")
    ws.cell(rows["2"], col["Decision"], "remove")
    ws.cell(rows["3"], col["Client comment"], "Only for flights within Ethiopia?")       # a note, not applied
    ws.cell(rows["4"], col["Category Code"], "G0005")
    wb.save(revised)
    out, table = tmp_path / "final.xlsx", tmp_path / "cats.csv"
    done = flows.apply_file(str(revised), "AAA", str(out), str(table), model=flows.Model(off=True))
    assert (len(done.drafts), len(done.final)) == (7, 5)

    cats = cat.load(str(table))
    assert [c.description for c in cats][:2] == ["Supply of Stationery and Toners",
                                                 "Provision of Travel Agency Services (IATA Registered Only)"]
    assert len(cats) == 5                                 # 7 rows: one removed, one duplicate folded
    assert cats[1].eligibility == ["IATA Registration Certificate"] and not cats[1].gate_question
    final = openpyxl.load_workbook(out)
    assert final["Client comments"]["B2"].value == "Only for flights within Ethiopia?"
    codes = [c.value for c in final["Final Categories"]["B"]]
    assert "G0005" in codes


def test_evaluation_counts_the_teams_categories_and_separations(tmp_path):
    from tendersure import refine as rf
    drafts = [rf.Draft("1", "Supply of ICT Equipment"),
              rf.Draft("2", "Provision of Cleaning, Gardening and Pest Control Services"),
              rf.Draft("3", "Supply of Pharmaceutical Products")]
    lines = [rf.Line("Supply of ICT Equipment", rf.KEPT, ["1"]),
             rf.Line("Provision of Cleaning Services", rf.SEPARATED, ["2"]),
             rf.Line("Provision of Pest Control Services", rf.SEPARATED, ["2"]),
             rf.Line("Supply of Pharmaceutical Products", rf.KEPT, ["3"])]
    team = ["Supply of ICT Equipment", "Provision of Cleaning Services", "Provision of Gardening Services",
            "Provision of Pest Control Services"]                       # pharmaceuticals removed by the client
    got = rf.evaluate(drafts, lines, team)
    assert got["team categories proposed"] == "3/4"                    # gardening not proposed
    assert got["team separations proposed"] == "1/1"
    assert got["proposals the team did not make"] == ["Supply of Pharmaceutical Products"]
    assert got["rows the team dropped (removed or merged)"] == 1


def test_only_eligibility_the_draft_states_is_applied():
    from tendersure import refine as rf
    from tendersure.naming import acronym_for
    drafts = [rf.Draft("1", "Provision of travel agency services (IATA Registered Only)", "T"),
              rf.Draft("2", "Supply of assorted pharmaceuticals", "M")]
    raw = {"lines": [{"from": ["1"], "status": "Kept", "description": "x",
                      "eligibility": ["IATA Registration Certificate", "Business Travel Licence"]},
                     {"from": ["2"], "status": "Kept", "description": "x",
                      "eligibility": ["Pharmacy Wholesale License"]}], "additional": []}
    travel, pharma = rf.check(raw, drafts, ["T", "M"])
    assert (travel.eligibility, travel.suggested_eligibility) == (["IATA Registration Certificate"],
                                                                  ["Business Travel Licence"])
    assert (pharma.eligibility, pharma.suggested_eligibility) == ([], ["Pharmacy Wholesale License"])
    assert acronym_for("IATA Registration Certificate") == "IATA"


def test_category_eligibility_documents_join_the_eligibility_section(corpus):
    from tendersure import glossary as gl, naming
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_template
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, "Provision of Travel Agency Services (IATA Registered Only)", src.client,
                      eligibility=["IATA Registration Certificate"]))
    finalise(p)
    naming.name_documents([p], gl.Glossary([]), naming.RuleDrafter())
    elig = [q.row for q in p.questions if q.row.eligibility]
    assert [r.criteria for r in elig] == ["Please attach a valid Trade license",
                                          "Please attach a valid IATA Registration Certificate"]
    iata = elig[1]
    assert (iata.section, iata.answer_type, iata.mandatory, iata.compliance_attach, iata.compliance) == \
           ("Eligibility", 5, True, True, 0)
    assert (iata.archive_short_name, iata.acronym) == ("IATA Registration Certificate", "IATA")
    assert check_template(to_sheet(p)) == [] and p.total() == 100


# --- gate question and applies to ---------------------------------------------------

def _audience_book():
    from tendersure import gating
    from tendersure.parser import Row
    companies = ["Sole Proprietor", "Private Limited Company", "Share Company"]
    book = gating.Book([gating.Audience("company name", "Company name", companies),
                        gating.Audience("attach tin certificate", "Attach TIN certificate", companies),
                        gating.Audience("full name as per national id or passport", "Full name", ["Individual Consultant"]),
                        gating.Audience("please attach valid id/passport", "Valid ID", ["Individual Consultant"])])
    donors = {"full name as per national id or passport": Row(
                  "s.xlsx", "SNV-1", 12, section="Company Information", criteria="Full name (as per National ID or passport)",
                  answer_type=1, marks=0, archive_short_name="Name"),
              "please attach valid id/passport": Row(
                  "s.xlsx", "SNV-1", 30, section="Registration and Statutory Requirements",
                  criteria="Please attach Valid ID/Passport", answer_type=1, marks=0, compliance=0, mandatory=True,
                  compliance_attach=True, archive_short_name="ID")}
    return book, donors


def test_a_gated_category_gets_its_business_types_and_audiences(corpus):
    from tendersure import gating
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_gate
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    types = ["Sole Proprietor", "Private Limited Company", "Individual Consultant"]
    p = g.plan(Target(src.code, src.description, src.client, gate_question=True, applies_to=types))
    book, donors = _audience_book()
    gating.apply(p, book, donors)
    finalise(p)
    rows = {q.row.criteria: q for q in p.questions}
    gate = rows["Organization type"].row
    assert (gate.gate_question, gate.options, gate.option_scores) == (True, ", ".join(types), "0,0,0")
    assert [q.row.criteria for q in p.questions if q.row.gate_question] == ["Organization type"]
    assert rows["Company name"].row.applies_to == "Sole Proprietor, Private Limited Company"   # no Share Company here
    assert rows["Attach TIN certificate"].row.applies_to == "Sole Proprietor, Private Limited Company"
    full = rows["Full name (as per National ID or passport)"].row
    assert (full.applies_to, full.section) == ("Individual Consultant", "Company Information")
    valid_id = rows["Please attach Valid ID/Passport"]
    assert (valid_id.row.applies_to, valid_id.row.answer_type) == ("Individual Consultant", 5)   # text box fixed
    assert rows["Attach evidence e.g. PO or award letter"].row.applies_to is None          # everyone
    office = rows["Attach evidence of physically registered office"]
    assert office.row.applies_to is None and "no past sheet says who answers it" in office.notes[-1]
    assert check_gate(to_sheet(p), True, types) == [] and p.total() == 100


def test_without_a_gate_the_columns_and_individual_questions_go(corpus):
    from tendersure import gating
    from tendersure.generate import PlannedRow, finalise, to_sheet
    from tendersure.parser import Row
    from tendersure.validate import check_gate
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, src.description, src.client))
    rows = {q.row.criteria: q.row for q in p.questions}
    org = rows["Organization type"]                                    # as an SNV skeleton has it
    org.options, org.option_scores, org.gate_question = "Individual Consultant, sole proprietor, plc", "0,0,0", True
    rows["Company name"].applies_to = "sole proprietor, plc"
    at = next(i for i, q in enumerate(p.rows) if q.kind == "question" and q.row is rows["Company name"]) + 1
    p.rows.insert(at, PlannedRow(kind="question", row=Row("", "", 0, section="Company Information",
                                 criteria="Full name (as per National ID or passport)", answer_type=1, marks=0,
                                 applies_to="Individual Consultant")))
    book, donors = _audience_book()
    gating.apply(p, book, donors)
    finalise(p)
    texts = [q.row.criteria for q in p.questions]
    assert "Full name (as per National ID or passport)" not in texts
    assert (org.options, org.option_scores, org.gate_question) == ("sole proprietor, plc", "0,0", False)
    assert not any(q.row.applies_to for q in p.questions)
    assert check_gate(to_sheet(p), False, []) == []


def test_a_category_whose_gate_disagrees_with_applies_to_is_not_written(corpus, tmp_path):
    from tendersure import categories as cat, flows
    cats = [cat.Category(1, "Supply of Office Stationery", "GENERAL", client="AAA"),
            cat.Category(2, "Provision of Consultancy Services", "CONSULTANCY", client="AAA", gate_question=True,
                         applies_to=["Sole Proprietor", "Share Company"])]               # no Individual Consultant
    cat.save(cats, str(tmp_path / "cats.csv"))
    r = flows.generate(flows.GenerateSettings(
        str(tmp_path / "cats.csv"), "AAA", "AAA-ET-2026-", str(tmp_path / "o.xlsx"), source=str(corpus),
        audience=str(tmp_path / "aud.csv"), glossary=str(tmp_path / "g.csv"), yes_no=str(tmp_path / "yn.csv"),
        model=flows.Model(off=True)))
    assert [c for c, _ in r.failed] == ["AAA-ET-2026-002"]
    assert r.failed[0][1].startswith("categories table: Gate Question is TRUE")
    assert [s for s in openpyxl.load_workbook(tmp_path / "o.xlsx").sheetnames] == ["AAA-ET-2026-001"]


# --- check after hand edits ------------------------------------------------------------

def _generated(corpus, tmp_path):
    """A workbook generate wrote from the synthetic history, its categories table and the result."""
    from tendersure import categories as cat, flows
    cats = [cat.Category(1, "Supply of Office Stationery and Toners", "GENERAL", client="AAA"),
            cat.Category(2, "Provision of Security and Guarding Services", "GENERAL", client="AAA")]
    cat.save(cats, str(tmp_path / "cats.csv"))
    r = flows.generate(flows.GenerateSettings(
        str(tmp_path / "cats.csv"), "AAA", "AAA-ET-2026-", str(tmp_path / "gen.xlsx"), source=str(corpus),
        audience=str(tmp_path / "aud.csv"), glossary=str(tmp_path / "g.csv"), yes_no=str(tmp_path / "yn.csv"),
        model=flows.Model(off=True)))
    return tmp_path / "gen.xlsx", tmp_path / "cats.csv", r


def _check(path, tmp_path, categories=None, against=None):
    """(errors found, the report's rows)."""
    from tendersure import flows
    out = tmp_path / "report.xlsx"
    r = flows.check(str(path), "AAA", categories, against, str(tmp_path / "yn.csv"), str(out), flows.Model(off=True))
    rows = list(openpyxl.load_workbook(out)["Findings"].iter_rows(min_row=2, values_only=True))
    return r.errors, rows


def test_a_generated_workbook_passes_the_check(corpus, tmp_path):
    gen, cats, _ = _generated(corpus, tmp_path)
    before = gen.read_bytes()
    errors, rows = _check(gen, tmp_path, str(cats))
    assert errors == 0 and [r for r in rows if r[3] == "error"] == []
    assert gen.read_bytes() == before                                   # the check never writes


def test_hand_edits_are_found(corpus, tmp_path):
    import shutil
    gen, cats, _ = _generated(corpus, tmp_path)
    edited = tmp_path / "edited.xlsx"
    shutil.copy(gen, edited)
    wb = openpyxl.load_workbook(edited)
    ws = wb["AAA-ET-2026-001"]
    find = lambda text: next(r for r in range(5, ws.max_row + 1) if ws.cell(r, 4).value == text)
    ws.cell(find("Attach evidence e.g. PO or award letter"), 5, 45)            # a mark changed
    ws.cell(find("Insert TIN number"), 12, 5)                                  # answer type retyped
    ws.cell(find("Company name"), 4, "Company name as registered with SNV")    # another client's name
    ws.cell(find("Organization type"), 9, "x")                                 # Kenya-only column (Evaluation)
    ws.title = "AAA-ET-2026-01"                                               # tab renamed
    wb.save(edited)
    errors, rows = _check(edited, tmp_path, str(cats), str(gen))
    rules = {r[2] for r in rows if r[0] == "AAA-ET-2026-01"}                  # findings carry the tab's name
    assert errors > 0                               # the CLI exits 1 on this
    assert {"SUM-ROWS", "MARKS-SPLIT", "AT-ASKS-TEXT", "OTHER-CLIENT", "KENYA-COLUMN", "CODE-TAB",
            "EDIT-CHANGED"} <= rules


def test_incomplete_reference_blocks_and_missing_sections_are_found():
    from tendersure.check import check_standard_blocks
    from tendersure.parser import Row, Sheet
    s = Sheet("f", "S", "S", "S", "d", "AAA", 4, 21)
    ref = lambda n, q: Row("f", "S", 0, section="Trade Reference", sub_section=f"Ref {n}", criteria=q)
    s.rows = [Row("f", "S", 0, section="Declaration", criteria="I/We declare"),
              ref(1, "Client Name"), ref(1, "Contact person"), ref(1, "Attach evidence Project1"),
              ref(2, "Client Name"), ref(2, "Attach evidence Project 2")]
    got = [(f.rule, f.message) for f in check_standard_blocks(s)]
    assert ("STD-SECTION", "No Submission section.") in got
    assert any(rule == "STD-BLOCK" and "'Ref 2' lacks" in m and "contact person" in m for rule, m in got)
    assert len(got) == 2                                    # "Project1" / "Project 2" are the same question


def test_the_ai_read_asks_once_per_distinct_question_and_only_warns(corpus, tmp_path, monkeypatch):
    from tendersure import check_ai
    gen, _, _ = _generated(corpus, tmp_path)
    from tendersure import paths
    monkeypatch.setattr(paths, "_current", paths.Paths(str(tmp_path)))
    t = FakeTransport({"problems": [{"id": 0, "kind": "wording", "reason": "unclear"}]})
    found = check_ai.read(str(gen), llm=OpenRouterLLM(model="m", transport=t))
    assert len(t.calls) == 1 and found and all(f.severity == "warning" and f.kind == "model" for f in found)
    assert "more sheets" in found[0].message                 # the same question in both sheets: read once
    check_ai.read(str(gen), llm=OpenRouterLLM(model="m", transport=t))
    assert len(t.calls) == 1                                 # cached: nothing asked again


def test_generate_refuses_a_sheet_the_check_finds_errors_in(corpus, tmp_path, monkeypatch):
    # History with errors is quarantined and never copied, so plant one where generation checks.
    from tendersure import check as ck
    from tendersure.validate import Finding
    real = ck.check_other_clients
    monkeypatch.setattr(ck, "check_other_clients", lambda s, c: real(s, c) + (
        [Finding(s.name, 9, "OTHER-CLIENT", "silent", "error", "names 'SNV'")] if s.name.endswith("002") else []))
    _, _, r = _generated(corpus, tmp_path)
    assert ("AAA-ET-2026-002", "1 check error(s): OTHER-CLIENT 1 (first: names 'SNV')") in r.failed
    assert openpyxl.load_workbook(tmp_path / "gen.xlsx").sheetnames == ["AAA-ET-2026-001"]


def test_a_named_text_question_gets_an_acronym_too(corpus):
    from tendersure import glossary as gl, naming
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_names
    p = _named_plan(corpus, shareholders=0)
    company = next(q.row for q in p.questions if q.row.criteria == "Company name")
    assert (company.answer_type, company.archive_short_name, company.acronym) == (1, "Company Name", None)
    naming.name_documents([p], gl.Glossary([]), naming.RuleDrafter())
    assert company.acronym == "CN"
    company.acronym = None                                   # as a hand edit might leave it
    assert [f.rule for f in check_names(to_sheet(p))] == ["NAMES-NO-ACRONYM"]


def test_major_and_minor_are_not_duplicates_and_nested_eligibility_is_read():
    from tendersure import refine as rf
    drafts = [rf.Draft("1", "Provision of Major Electrical Works"), rf.Draft("2", "Provision of Minor Electrical Works"),
              rf.Draft("3", "Provision of repairs of fire fighting equipment"),
              rf.Draft("4", "Provision of Repairs of Fire Fighting Equipment")]
    assert rf.duplicates(drafts) == {"4": "3"}
    assert rf.rule_eligibility("Provision of Training Services (Cash and Learning Partnership (CALP)-Certified "
                               "Trainers Only)") == ["CALP Certificate"]
    assert rf.rule_eligibility("Provision of Travel Agency Services (IATA Registered Only)") == \
           ["IATA Registration Certificate"]


def test_a_kenyan_eligibility_section_keeps_its_typed_rows():
    from tendersure.generate import Plan, PlannedRow, Target, finalise, to_sheet
    from tendersure.parser import Row
    from tendersure.validate import check_eligibility
    E = "Eligibility"
    rows = [Row("", "", 0, section="Company Information", criteria="Company name", answer_type=1, marks=0),
            Row("", "", 0, section=E, criteria="Attach company registration certificate", answer_type=5, marks=0,
                compliance_attach=True, eligibility=True),
            Row("", "", 0, section=E, criteria="Insert company registration certificate number", answer_type=1, marks=0),
            Row("", "", 0, section=E, criteria="Does your company have a valid business permit ? If yes attach a copy",
                answer_type=5, marks=0, compliance_attach=True),
            Row("", "", 0, section="Trade Reference", criteria="Attach evidence", answer_type=5, marks=100,
                compliance=100, compliance_attach=True)]
    p = Plan(Target("NRC_2027_28_005", "Supply of ICT Equipment", "NRC"), None, [],
             [PlannedRow(kind="question", row=r) for r in rows] + [PlannedRow(kind="total")], 21)
    finalise(p)
    by = lambda start: next(q.row for q in p.questions if q.row.criteria.startswith(start))
    typed = by("Insert company registration")
    assert (typed.answer_type, typed.eligibility) == (1, False)            # a typed row stays typed
    permit = by("Does your company have a valid")
    assert permit.eligibility and permit.mandatory                          # Kenya's trade licence
    assert [q.row.criteria for q in p.questions].count("Please attach a valid Trade license") == 0
    assert check_eligibility(to_sheet(p)) == []


def test_a_network_failure_is_a_model_error_steps_can_fall_back_on(monkeypatch):
    import urllib.error
    import urllib.request
    from tendersure import llm, yesno

    def down(*a, **k):
        raise urllib.error.URLError("[Errno 8] nodename nor servname provided, or not known")
    monkeypatch.setattr(urllib.request, "urlopen", down)
    with pytest.raises(llm.LLMError, match="network"):
        llm._http("https://example.invalid", {}, {})
    judge = yesno.ModelJudge(llm=OpenRouterLLM(model="m", transport=llm._http))
    got = judge.judge([yesno.Need("AAA", "k", "Have you had any contracts terminated?")])
    assert [(v.yes_is, v.status) for v in got] == [("bad", yesno.RULE_DRAFTED)]     # fell back, no crash


def test_underscore_sheet_codes_and_names_inside_them_are_hidden():
    from tendersure.privacy import Anonymiser, PrivacyError, register_clients
    register_clients(["NRC"])
    anon = Anonymiser("NRC")
    text = ("NEW CATEGORY: NRC_2027_28_030: Provision of Reception Services\n"
            "Draft copied from: NRC_2027_28_020: Provision of Freight Clearing\n"
            "Is your company a current supplier of Norwegian Refugee Council?")
    out = anon.anonymise(text)
    assert "NRC" not in out and "Norwegian" not in out and "[REF-" in out and "[CLIENT]" in out
    assert anon.leaks(out) == []
    with pytest.raises(PrivacyError):
        anon.guard("Built from NRC_2027_28_020")           # the final scan refuses a raw code
    assert "ERCS" not in Anonymiser("ERCS").anonymise("ERCS-ET-2026-002 and SNV-ET-2026_006")


# --- research: our data first, then the web ---------------------------------------------

def _past(code, desc, rows):
    from tendersure.parser import Row, Sheet
    s = Sheet("f.xlsx", code, f"{code}: {desc}", code, desc, code.split("-")[0].split("_")[0], 4, 21)
    s.rows = [Row("f.xlsx", code, 5 + i, section=sec, criteria=c, answer_type=5, compliance_attach=True)
              for i, (sec, c) in enumerate(rows)]
    return s


def test_research_answers_from_the_bank_first_and_licences_stay_national():
    from tendersure import research as rs
    tech = "Technical Specifications-TECH"
    bank = rs.BankKnowledge([
        _past("AAA-ET-2026-019", "Supply of Assorted Pharmaceuticals",
              [(tech, "Please attach a valid licence from the Ethiopian Food and Drug Authority"),
               ("Human Resource- HRM", "Attach resumes/certificates of the pharmacists"),
               ("Registration and Statutory Requirements", "Attach TIN certificate")]),
        _past("BBB_2027_28_017", "Supply of Pharmaceutical Products",
              [(tech, "Attach a Pharmacy and Poisons Board licence (Kenya)")])])
    assert rs.sheet_country(bank.sheets[1]) == "KE"                 # no code country: the questions say Kenya
    table, calls = rs.Table([]), []

    class Web:                                                       # stands in for the web: records the asks
        def research(self, topic, country, kinds, client=None):
            calls.append((topic, country, tuple(kinds)))
            return []
    new = rs.research(["Supply of assorted pharmaceuticals"], "ET", ["names", "licences"], table, bank, Web())
    lic = next(f for f in new if f.kind == "licences")
    assert [x["name"] for x in lic.proposal["licences"]] == \
        ["Please attach a valid licence from the Ethiopian Food and Drug Authority"]   # no CV, TIN or Kenyan licence
    assert next(f for f in new if f.kind == "names").origin == rs.BANK and calls == []  # the bank answered: no web
    rs.research(["Supply of assorted pharmaceuticals"], "ET", ["names", "licences"], table, bank, Web())
    assert calls == [] and len(table.findings) == 2                 # researched once
    rs.research(["Provision of drone survey services"], "ET", ["names"], table, bank, Web())
    assert calls == [("Provision of drone survey services", "ET", ("names",))]   # nothing known: the web


def test_web_findings_carry_the_pages_the_search_used():
    from tendersure import research as rs
    from tendersure.llm import GeminiLLM

    def transport(url, headers, body):
        assert body["tools"] == [{"google_search": {}}] and "responseJsonSchema" not in body["generationConfig"]
        text = json.dumps({"names": {"technical_name": "Water Treatment Chemicals",
                                     "includes": ["chlorine tablets", "aluminium sulphate"]}})
        grounding = {"groundingChunks": [{"web": {"uri": "https://example.org/wtc", "title": "Water treatment"}}]}
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP",
                                "groundingMetadata": grounding if transport.ground else {}}]}
    transport.ground = True
    web = rs.WebResearcher(llm=GeminiLLM(model="m", transport=transport, paid_tier=True))
    got = web.research("Supply of Aqua Water Purification Chemicals", "ET", ["names"])
    assert got[0].proposal["technical_name"] == "Water Treatment Chemicals"
    assert got[0].sources == ["Water treatment | https://example.org/wtc"] and got[0].origin == rs.WEB
    transport.ground = False
    assert web.research("Supply of Aqua Water Purification Chemicals", "ET", ["names"]) == []   # no source: dropped


def test_research_prompts_name_no_client_and_differ_only_by_country():
    from tendersure import research as rs
    from tendersure.privacy import CLIENT_ALIASES
    et = rs.build_prompt("Supply of Generators", "ET", list(rs.KINDS))
    ke = rs.build_prompt("Supply of Generators", "KE", list(rs.KINDS))
    names = [a for aliases in CLIENT_ALIASES.values() for a in aliases]
    for text in (et, ke, rs.SYSTEM):
        assert not any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)
    assert et.replace("Country: ET", "Country: KE") == ke


def test_research_review_confirms_and_corrects(tmp_path):
    from tendersure import research as rs
    k = rs.topic_key("Supply of x")
    t = rs.Table([rs.Finding("names", k, "ET", "Supply of x", {"technical_name": "X"}, ["s"], rs.WEB),
                  rs.Finding("licences", k, "ET", "Supply of x", {"licences": [{"name": "A"}]}, ["s"], rs.WEB)])
    assert rs.write_review(t.findings, str(tmp_path / "r.xlsx")) == 2
    wb = openpyxl.load_workbook(tmp_path / "r.xlsx")
    wb["Research"]["H2"], wb["Research"]["H3"] = "ok", "Licence B; Standard C"
    wb.save(tmp_path / "r.xlsx")
    assert rs.apply_review(str(tmp_path / "r.xlsx"), t) == (2, [])
    rs.save(t, str(tmp_path / "research.csv"))
    back = rs.load(str(tmp_path / "research.csv"))
    assert [f.status for f in back.findings] == [rs.CONFIRMED, rs.CONFIRMED]
    assert [x["name"] for x in back.lookup("licences", "Supply of x", "ET").proposal["licences"]] == ["Licence B", "Standard C"]


def _findings(*items):
    from tendersure import research as rs
    return rs.Table([rs.Finding(kind, rs.topic_key(topic), "ET", topic, proposal, [src], rs.WEB)
                     for kind, topic, proposal, src in items])


def test_refine_shows_research_with_sources_and_keeps_proposals(tmp_path):
    from tendersure import flows, refine as rf, research as rs
    draft = _draft_book(tmp_path / "rc.xlsx", RC)
    revised = tmp_path / "revised.xlsx"
    flows.propose_file(draft, "AAA", out=str(revised), bank=str(tmp_path / "none"), research=str(tmp_path / "r.csv"),
                       model=flows.Model(off=True))
    _, before = rf.read_revised(str(revised))
    wb = openpyxl.load_workbook(revised)                    # the team has started answering
    ws = wb["Revised"]
    col = {c.value: c.column for c in ws[1]}
    first = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, col["Status"]).value)
    ws.cell(first, col["Decision"], "edit")
    ws.cell(first, col["Client comment"], "Supply of Stationery")
    wb.save(revised)
    office = "Supply of Office Stationery"
    rs.save(_findings(("names", office, {"technical_name": "Supply of Office Stationery and Consumables",
                                         "usually_split_as": ["Paper and stationery", "Toners and cartridges"]},
                       "Stationery | https://example.org/s"),
                      ("licences", office, {"licences": [{"name": "Trade Licence for Wholesale"}]},
                       "Licences | https://example.org/l")), str(tmp_path / "r.csv"))
    flows.research(str(revised), "AAA", ["names", "licences"], country="ET", out=str(tmp_path / "rv.xlsx"),
                   bank=str(tmp_path / "none"), table_path=str(tmp_path / "r.csv"), model=flows.Model(off=True))
    _, after = rf.read_revised(str(revised))
    line = next(ln for ln in after if ln.description == office)
    assert line.technical_name == "Supply of Office Stationery and Consumables"
    assert line.usual_split == ["Paper and stationery", "Toners and cartridges"]
    assert "Trade Licence for Wholesale" in line.suggested_eligibility and not line.eligibility
    assert any("https://example.org/s" in x for x in line.research_sources)
    assert [(l.description, l.status, l.decision, l.comment) for l in after] == \
           [(l.description, l.status, d, c) for l, (d, c) in zip(before, [("edit", "Supply of Stationery")] +
                                                                   [("", "")] * (len(before) - 1))]


def test_generate_researches_only_categories_the_bank_has_no_source_for(corpus):
    from tendersure.generate import apply_research, unknown_abbreviations
    from tendersure.parser import Row
    from tendersure import research as rs
    sheets, g = _gen(corpus)
    p = g.plan(Target("AAA-ET-2026-009", "Provision of Drone Survey Services", "AAA"))
    covered = g.plan(Target("AAA-ET-2026-001", "Supply of Office Stationery and Toners", "AAA"))
    assert p.weak_match and not covered.weak_match
    topic = "Provision of Drone Survey Services"
    table = _findings(("questions", topic, {"questions": [
                           {"question": "Attach your civil aviation drone operator certificate", "answer_type": 5},
                           {"question": "How many drones do you operate?", "answer_type": 1}]},
                       "Drones | https://example.org/d"),
                      ("licences", topic, {"licences": [{"name": "Remote Pilot Licence", "issuer": "the aviation authority"}]},
                       "Aviation | https://example.org/a"),
                      ("documents", "docs", {"documents": [{"abbreviation": "RPAS", "official_name":
                                                            "Remotely Piloted Aircraft System permit"}]},
                       "Permits | https://example.org/p"))
    p.questions[0].row.criteria, p.questions[0].row.answer_type = "Attach your RPAS permit", 5
    assert unknown_abbreviations([p], set()) == ["RPAS"]
    apply_research(p, table, "ET")
    apply_research(covered, table, "ET")
    added = [q for q in p.questions if q.origin == "newly drafted"]
    assert [q.row.criteria for q in added] == ["Attach your civil aviation drone operator certificate",
                                               "How many drones do you operate?"]
    assert all(q.row.marks == 0 for q in added) and added[0].row.attachment
    assert "https://example.org/d" in added[0].notes[0]
    assert any("Remote Pilot Licence" in n and "not applied" in n for n in p.notes)
    assert any("Remotely Piloted Aircraft System permit" in n for n in p.questions[0].notes)
    assert not any(q.origin == "newly drafted" and "drone" in q.row.criteria for q in covered.questions)


def _grounded(payload, sources):
    """A Gemini stand-in answering `payload` with these grounding pages."""
    from tendersure.llm import GeminiLLM

    def transport(url, headers, body):
        chunks = [{"web": {"uri": u, "title": t}} for t, u in sources]
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}, "finishReason": "STOP",
                                "groundingMetadata": {"groundingChunks": chunks}}]}
    return GeminiLLM(model="m", transport=transport, paid_tier=True)


def test_web_licences_drop_what_every_supplier_holds_and_links_resolve():
    from tendersure import research as rs
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC"
    web = rs.WebResearcher(llm=_grounded({"licences": [
        {"name": "Business License (Trade License)"}, {"name": "Commercial Registration Certificate"},
        {"name": "TIN Certificate"}, {"name": "Certificate of Competency for Building Contractor",
                                      "issuer": "Ministry of Urban Development"}]}, [("moud.gov.et", redirect)]),
        resolve=lambda u: "https://moud.gov.et/coc" if "redirect" in u else u)
    f = web.research("Provision of Building Construction Works", "ET", ["licences"])[0]
    assert [x["name"] for x in f.proposal["licences"]] == ["Certificate of Competency for Building Contractor"]
    assert f.sources == ["moud.gov.et | https://moud.gov.et/coc"]
    only_generic = rs.WebResearcher(llm=_grounded({"licences": [{"name": "Trade License"}]}, [("x", "https://x")]))
    assert only_generic.research("Supply of Stationery", "ET", ["licences"]) == []


def test_topics_the_web_found_nothing_on_are_reported(caplog):
    import logging
    from tendersure import research as rs
    web = rs.WebResearcher(llm=_grounded({"names": {"technical_name": "X"}}, []))      # no grounding: no source
    with caplog.at_level(logging.INFO, logger="tendersure"):
        rs.research(["Provision of Obscure Services"], "ET", ["names"], rs.Table([]), None, web)
    assert web.unanswered == ["Provision of Obscure Services"]
    assert "found nothing sourced for 1 topic(s)" in caplog.text and "Provision of Obscure Services" in caplog.text


def test_tied_bank_licences_come_out_in_the_same_order_every_run():
    from tendersure import research as rs
    tech = "Technical Specifications-TECH"
    rows = [(tech, "Attach the national standards certificate"), (tech, "Attach the dealership authorization")]
    for order in (rows, rows[::-1]):
        bank = rs.BankKnowledge([_past("AAA-ET-2026-033", "Supply of Toiletries and Consumables", order)])
        got = [x["name"] for x in bank.licences("Supply of Toiletries", "ET").proposal["licences"]]
        assert got == sorted(got)


def test_tidy_brings_stored_findings_up_to_the_rules_without_searching():
    from tendersure import research as rs
    k = rs.topic_key("Supply of x")
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/Q"
    t = rs.Table([rs.Finding("licences", k, "ET", "Supply of x", {"licences": [{"name": "Trade License"}]},
                             ["a | " + redirect], rs.WEB),
                  rs.Finding("names", k, "ET", "Supply of x", {"technical_name": "X"}, ["a | " + redirect], rs.WEB),
                  rs.Finding("licences", rs.topic_key("y"), "ET", "y", {"licences": [{"name": "Trade License"}]},
                             ["team"], rs.WEB, rs.CONFIRMED)])
    assert rs.tidy(t, resolve=lambda u: "https://a/page") == (1, 1)
    assert t.lookup("licences", "Supply of x", "ET") is None                  # only generic: gone
    assert t.lookup("names", "Supply of x", "ET").sources == ["a | https://a/page"]
    assert t.lookup("licences", "y", "ET") is not None                         # the team's word stands


def test_links_resolve_in_parallel_and_fall_back_to_get(monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    from tendersure import research as rs
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    seen = set()

    def urlopen(req, timeout):
        seen.add(threading.current_thread().name)
        if req.get_method() == "HEAD":                         # this site refuses HEAD
            raise urllib.error.URLError("method not allowed")

        class Resp:
            def geturl(self):
                return "https://site.example/" + req.full_url.rsplit("/", 1)[1]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    links = [redirect + f"p{i}" for i in range(20)] + ["https://plain.example/x"]
    got = rs.resolve_links(links)
    assert got[redirect + "p7"] == "https://site.example/p7" and "https://plain.example/x" not in got
    assert len(seen) > 1                                         # many at a time


# --- the category's own requirements (spec 07 R4) -------------------------------------

def _plan_with_hr(corpus, **wants):
    """A plan on AAA's service sheet with a Human Resource section of two CV
    rows of 5 marks (taken from the trade reference, so the sheet stays at 100)."""
    from tendersure.generate import ADAPTED, insert_rows
    from tendersure.parser import Row
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, "Provision of Minor Construction Works", src.client, **wants))
    cv = lambda role: Row("", "", 0, section="Human Resource- HRM", sub_section="N/A", answer_type=5, marks=5.0,
                          criteria=f"Attach evidence (CV, contract and education experience) for an {role}",
                          compliance_attach=True)
    insert_rows(p, "hr", [cv("Architect"), cv("Structural Engineer")], ADAPTED, "test", "test")
    for q in p.questions:
        if section_key(q.row.section) == "hr":
            q.row.marks = q.row.compliance = 5.0
        elif q.row.marks == 50:
            q.row.marks = q.row.compliance = 40.0
    assert p.total() == 100
    return p


def _checked(p):
    """The template check's findings once the rows are named, as generation does."""
    from tendersure import glossary as gl, naming
    from tendersure.generate import to_sheet
    from tendersure.validate import check_template
    naming.name_documents([p], gl.Glossary([]), naming.RuleDrafter())
    return check_template(to_sheet(p))


def test_hr_roles_take_the_skeleton_s_role_rows_and_their_marks(corpus):
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_template
    p = _plan_with_hr(corpus, hr=["Project Manager", "Site engineer/Residential Engineer minimum 5 years", "Foreman"])
    finalise(p)
    hr = [q.row for q in p.questions if section_key(q.row.section) == "hr"]
    assert [(r.criteria, r.marks) for r in hr] == [
        ("Attach evidence (CV, contract and education/experience) for Project Manager", 5),
        ("Attach evidence (CV, contract and education/experience) for Site engineer/Residential Engineer "
         "with at least 5 years of experience", 5),
        ("Attach evidence (CV, contract and education/experience) for Foreman", 0)]
    assert p.total() == 100 and _checked(p) == []


def test_skeleton_roles_left_over_go_and_their_marks_stay_in_the_section(corpus):
    from tendersure.generate import finalise
    p = _plan_with_hr(corpus, hr=["Structural Engineer"])
    finalise(p)
    hr = [q.row for q in p.questions if section_key(q.row.section) == "hr"]
    assert [(r.criteria, r.marks) for r in hr] == [
        ("Attach evidence (CV, contract and education/experience) for Structural Engineer", 10)]
    assert [why for _, why in p.dropped] == ["role not in the category's HR column (Structural Engineer)"]
    assert p.total() == 100


def test_technical_items_replace_matching_rows_or_arrive_with_no_marks(corpus):
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_template
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-002")
    p = g.plan(Target(src.code, "Supply and Installation of Solar Equipment", src.client,
                      technical=["MAF/DAF", "(Mixer, Pick up, Dump Truck)"]))
    finalise(p)
    tech = [(q.row.criteria, q.row.marks) for q in p.questions if section_key(q.row.section) == "technical"]
    assert ("Please attach MAF/DAF", 10) in tech                          # the DAF row, its marks kept
    assert ("Please attach evidence of ownership or rent agreement for: Mixer, Pick up, Dump Truck", 0) in tech
    assert ("Do you offer extended warranty on goods sold Yes/No", 10) in tech       # not matched: kept
    assert p.total() == 100 and _checked(p) == []


def test_licence_items_are_asked_in_the_eligibility_section(corpus):
    from tendersure import glossary as gl, naming
    from tendersure.generate import finalise, to_sheet
    from tendersure.validate import check_template
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    p = g.plan(Target(src.code, "Provision of Minor Construction Works", src.client,
                      licence=["min G5/BC5 or above", "Consultancy certification"]))
    finalise(p)
    naming.name_documents([p], gl.Glossary([]), naming.RuleDrafter())
    elig = [q.row for q in p.questions if section_key(q.row.section) == "eligibility"]
    assert [(r.criteria, r.answer_type, r.eligibility) for r in elig] == [
        ("Please attach a valid Trade license", 5, True),
        ("Please specify your contractor grade/level", 1, False),
        ("Please attach a valid grade/competence certificate from the relevant authority "
         "(required: min G5/BC5 or above)", 5, True),
        ("Please attach a valid consultancy certification", 5, True)]
    assert all(r.archive_short_name and r.acronym for r in elig)
    assert p.total() == 100 and check_template(to_sheet(p)) == []


def test_the_requirements_reach_the_model_as_text():
    from tendersure.requirements import stated
    c = Category(4, "Provision of Minor Construction Works", licence=["min G5"], hr=["Foreman"])
    assert stated(c) == ("licence / contractor grade: min G5; key staff: Foreman (rows asking for each are "
                         "added automatically; do not add them)")
    assert stated(Category(1, "Supply of Stationery")) == ""


def test_a_category_with_requirement_columns_is_written_and_passes_the_check(corpus, tmp_path):
    from tendersure import categories as cat, flows
    cat.save([cat.Category(1, "Provision of Minor Construction Works", "ENGINEERING WORKS", client="AAA",
                           licence=["min G5/BC5 or above"], hr=["Project Manager", "Foreman"],
                           technical=["(Dump Truck, Mixer)"])], str(tmp_path / "cats.csv"))
    r = flows.generate(flows.GenerateSettings(
        str(tmp_path / "cats.csv"), "AAA", "AAA-ET-2026-", str(tmp_path / "gen.xlsx"), source=str(corpus),
        audience=str(tmp_path / "aud.csv"), glossary=str(tmp_path / "g.csv"), yes_no=str(tmp_path / "yn.csv"),
        model=flows.Model(off=True)))
    assert (r.written, r.failed) == (["AAA-ET-2026-001"], [])
    texts = [q.row.criteria for q in r.plans[0].questions]
    assert "Please specify your contractor grade/level" in texts
    assert "Attach evidence (CV, contract and education/experience) for Foreman" in texts
    assert "Please attach evidence of ownership or rent agreement for: Dump Truck, Mixer" in texts
    errors, _ = _check(tmp_path / "gen.xlsx", tmp_path, str(tmp_path / "cats.csv"))
    assert errors == 0


# --- the client's brief (spec 07 B) -------------------------------------------------------

BRIEF = "Works contractors must own a dump truck; ask for site engineers with 5 years' experience."


class _Recorder:
    """A model that records each prompt and answers with an empty reply of the asked shape."""

    def __init__(self, reply):
        self.reply, self.prompts, self.last_model = reply, [], "fake"

    def complete_json(self, system, prompt, schema, name, client=None, **kw):
        self.prompts.append(prompt)
        return json.loads(json.dumps(self.reply))


def test_the_brief_comes_first_in_the_four_model_steps(corpus, tmp_path):
    from tendersure import brief as cb, check_ai, refine as rf, reviewer, selectors
    drafts = rf.read_draft(_draft_book(tmp_path / "rc.xlsx", RC))
    m = _Recorder({"lines": [], "additional": []})
    rf.ModelRefiner(llm=m).propose(drafts, "AAA", [], None, BRIEF)
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    plan = g.plan(Target(src.code, "Provision of Minor Construction Works", src.client, brief=BRIEF))
    gen, _, _ = _generated(corpus, tmp_path)
    c = _Recorder({"problems": []})
    check_ai.read(str(gen), llm=c, brief=BRIEF)
    prompts = [m.prompts[0], selectors.build_prompt(plan), reviewer.build_prompt(plan, []), c.prompts[0]]
    for p in prompts:
        assert p.startswith(cb.HEAD + "\n" + BRIEF + "\n")
    plan.target.brief = ""
    assert BRIEF not in selectors.build_prompt(plan)                     # no brief, no block


def test_what_the_brief_led_to_is_labelled(corpus):
    from tendersure import brief as cb
    from tendersure.selectors import normalise_proposal
    sheets, g = _gen(corpus)
    src = next(s for s in sheets if s.name == "AAA-ET-2026-003")
    plan = g.plan(Target(src.code, "Provision of Minor Construction Works", src.client, brief=BRIEF))
    raw = {"drop": [], "reword": [], "add": [], "brief_not_applied": [{"request": "60 marks for HR",
                                                                      "why": "marks are the team's"}],
           "drafts": [{"section": "technical", "criteria": "Do you own a dump truck?", "answer_type": 4,
                       "options": "", "reason": "the client asks for it", "from_brief": True},
                      {"section": "technical", "criteria": "Attach your fleet list", "answer_type": 5,
                       "options": "", "reason": "works need vehicles", "from_brief": False}]}
    prop = normalise_proposal(plan, raw, "fake")
    assert [d["reason"] for d in prop["drafts"]] == [f"{cb.FROM_BRIEF}: the client asks for it", "works need vehicles"]
    assert prop["brief_not_applied"] == ["60 marks for HR: marks are the team's"]

    from tendersure import refine as rf
    drafts = [rf.Draft("1", "Supply of Stationery")]
    lines = rf.check({"lines": [{"from": ["1"], "status": "Renamed", "description": "Supply of Office Stationery",
                                 "reason": "the client's wording", "from_brief": True}], "additional": []},
                     drafts, rf.groups_of(drafts))
    assert lines[0].reason.startswith(f"{cb.FROM_BRIEF}: the client's wording")


def test_without_a_model_the_qa_report_says_the_brief_was_not_applied(corpus, tmp_path):
    from tendersure import categories as cat, flows
    cat.save([cat.Category(1, "Supply of Office Stationery and Toners", "GENERAL", client="AAA")],
             str(tmp_path / "cats.csv"))
    r = flows.generate(flows.GenerateSettings(
        str(tmp_path / "cats.csv"), "AAA", "AAA-ET-2026-", str(tmp_path / "gen.xlsx"), source=str(corpus),
        audience=str(tmp_path / "aud.csv"), glossary=str(tmp_path / "g.csv"), yes_no=str(tmp_path / "yn.csv"),
        model=flows.Model(off=True), brief=BRIEF))
    texts = [c.value for c in openpyxl.load_workbook(r.qa)["Decisions"]["A"] if c.value]
    assert "Client's brief not applied: no model." in texts


def test_a_brief_is_read_from_a_file_or_taken_as_text(tmp_path):
    from tendersure import flows
    f = tmp_path / "brief.txt"
    f.write_text(BRIEF + "\n")
    assert flows.load_brief(str(f)) == BRIEF == flows.load_brief(f"  {BRIEF} ")
    assert flows.load_brief(None) == ""


def test_near_identical_tabs_with_spaces_and_brackets_in_their_names_are_graded(corpus):
    from copy import deepcopy
    from tendersure import trust
    sheets = load_corpus(str(corpus))[:1]
    a, b = deepcopy(sheets[0]), deepcopy(sheets[0])
    a.name, b.name, a.code, b.code, a.client, b.client = \
        "PSI General with Technical", "PSI Eng con (2)", None, None, None, None
    grades = trust.grade([a, b])                          # the other tab's name is read whole
    assert [g.tier for g in grades.values()].count(trust.SUPERSEDED) == 1
    assert any(r.startswith("earlier copy of PSI ") for g in grades.values() for r in g.reasons)
