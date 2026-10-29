"""Excel reports for the people who act on the results.

  validation_report.xlsx  what is wrong with the existing sheets, by rule
  bank.xlsx               the question bank, the written answer-type
                          reference, and the conflicts awaiting a ruling
                          (fill in the Decision column and pass the file
                          back with --decisions)
  qa_report.xlsx          for each generated sheet: where it came from,
                          which rows to read, the marks split and its source
"""

import json
from collections import Counter, defaultdict
from typing import Dict, List

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .bank import Bank
from .generate import ADAPTED, NEW, Plan
from .parser import Sheet, section_key
from .schema import ANSWER_TYPES
from .validate import Finding

HEAD_FILL = PatternFill("solid", fgColor="FFC1E5F5")
READ_FILL = PatternFill("solid", fgColor="FFFFF2CC")
BOLD = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")

RULE_HELP = {
    "AT-MISSING": "No answer type. The upload rejects it.",
    "AT-RANGE": "Answer type outside 1-5.",
    "AT-NO-OPTIONS": "Select question with no options.",
    "AT-STRAY-OPTIONS": "Options listed on a question type that ignores them.",
    "AT-NO-UPLOAD-FLAG": "Upload question with neither L nor M set. Common in ERCS; platform behaviour unconfirmed.",
    "AT-TEXT-SAYS-ATTACH": "Asks for a document but gives the supplier a text box.",
    "AT-ASKS-TEXT": "Asks for a typed value but gives the supplier an upload control.",
    "OPT-COUNT": "Number of options differs from number of scores.",
    "OPT-NO-SCORES": "Options with no scores.",
    "OPT-SCORE-NaN": "A score that is not a number.",
    "OPT-MAX-MARKS": "Best option score differs from the row's marks: unreachable or uncounted points.",
    "SUM-NO-LABEL": "Subtotal row without a 'Sub Total' label.",
    "SUM-LABEL-COLUMN": "Sub Total label typed in the wrong column.",
    "SUM-SPLIT": "Subtotal does not equal System + Compliance.",
    "SUM-TOTAL-SPLIT": "Total does not equal System + Compliance.",
    "SUM-ROWS": "Subtotal or total disagrees with the question rows it covers.",
    "SUM-MISMATCH": "Subtotals do not add up to the total.",
    "SUM-NOT-100": "Total is not 100.",
    "SUM-NO-TOTAL": "No Total Marks row.",
    "TXT-PLACEHOLDER": "Client name left blank when the sheet was copied.",
    "TXT-LINEBREAK": "Line break inside question text.",
    "UP-BOTH-FLAGS": "Both L and M upload flags set.",
    "UP-COMP-NO-MARKS": "Compliance upload (M) with no compliance marks.",
    "ID-NO-CLIENT": "Sheet code has lost its client prefix.",
    "SHEET-NEAR-DUP": "Near-identical copy of another sheet; inflates bank frequencies.",
}


def _table(ws, headers: List[str], rows: List[list], widths: Dict[str, float] = None):
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = BOLD, HEAD_FILL
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(12, len(h) + 2))
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = WRAP


# --- validation --------------------------------------------------------------

def write_validation_report(sheets: List[Sheet], findings: List[Finding], path: str, grades=None):
    from .trust import year_of
    wb = openpyxl.Workbook()
    ws = wb.active
    if grades:
        ws.title = "Health"
        groups = defaultdict(list)
        for s in sheets:
            groups[(s.client or "(unknown)", year_of(s) or "")].append(s)
        rows = []
        for (client, year), ss in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            t = Counter(grades[s.name].tier for s in ss)
            top = Counter(r for s in ss for r in grades[s.name].reasons if not r.startswith("earlier"))
            rows.append([client, year, len(ss), t["clean"], t["correctable"], t["quarantined"], t["superseded"],
                         ", ".join(f"{r} ({n})" for r, n in top.most_common(4))])
        _table(ws, ["Client", "Year", "Sheets", "Clean", "Fixed on reuse", "Quarantined", "Superseded",
                    "Main problems"], rows, {"Main problems": 60})
        ws.append([])
        for line in (
            "No review is needed here: every sheet is graded automatically.",
            "Fixed on reuse: errors the generator corrects whenever the sheet is copied.",
            "Quarantined: broken marks or identity. Never copied; its wording still counts in the bank.",
            "Superseded: an earlier copy of another sheet. Ignored.",
            "Fix a live sheet only if suppliers are answering it now; see Findings for the rows.",
        ):
            ws.append([line])
        _table(wb.create_sheet("Sheet grades"), ["Sheet", "Client", "Year", "Grade", "Why", "Source file"],
               [[s.name, s.client, year_of(s), grades[s.name].tier, ", ".join(grades[s.name].reasons),
                 s.source_file] for s in sheets], {"Sheet": 22, "Why": 50, "Source file": 34})
        ws = wb.create_sheet("Summary")
    else:
        ws.title = "Summary"
    by_rule = Counter(f.rule for f in findings)
    meta = {f.rule: f for f in findings}
    order = sorted(by_rule, key=lambda r: (meta[r].kind != "platform", meta[r].severity != "error", -by_rule[r]))
    _table(ws, ["Rule", "Class", "Severity", "Count", "Sheets affected", "What it means"],
           [[r, meta[r].kind, meta[r].severity, by_rule[r],
             len({f.sheet for f in findings if f.rule == r}), RULE_HELP.get(r, "")] for r in order],
           {"What it means": 70, "Rule": 22})
    ws.append([])
    ws.append(["platform = the upload will reject it (inferred; confirm with the platform's real error messages)."])
    ws.append(["silent = the upload accepts it and the supplier gets the wrong behaviour."])

    _table(wb.create_sheet("Findings"), ["Sheet", "Row", "Rule", "Class", "Severity", "Problem", "Suggested fix"],
           [[f.sheet, f.excel_row, f.rule, f.kind, f.severity, f.message, f.suggestion]
            for f in sorted(findings, key=lambda f: (f.sheet, f.excel_row or 0))],
           {"Sheet": 20, "Rule": 22, "Problem": 70, "Suggested fix": 50})

    per = defaultdict(Counter)
    for f in findings:
        per[f.sheet][f.kind] += 1
        per[f.sheet][f.severity] += 1
    _table(wb.create_sheet("By sheet"),
           ["Sheet", "Source file", "Questions", "Platform", "Silent", "Errors", "Warnings"],
           [[s.name, s.source_file, len(s.rows), per[s.name]["platform"], per[s.name]["silent"],
             per[s.name]["error"], per[s.name]["warning"]] for s in sheets],
           {"Sheet": 22, "Source file": 34})
    wb.save(path)
    return path


# --- bank --------------------------------------------------------------------

def write_bank(bank: Bank, path: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Answer types"
    counts = Counter()
    for e in bank.entries.values():
        counts.update(e.answer_types)
    evidence = bank.answer_type_evidence()
    rows = []
    for code, spec in ANSWER_TYPES.items():
        needs = ("options + one score per option" if spec["requires_options"]
                 else "L (Attachment) or M (Compliance) flag" if spec["requires_attachment"] else "-")
        rows.append([code, spec["name"], spec["supplier_sees"], needs, counts[code],
                     "\n".join(evidence.get(code, [])[:4])])
    _table(ws, ["Code", "Name", "Supplier sees", "Requires", "Uses in corpus", "Real examples"], rows,
           {"Supplier sees": 34, "Requires": 30, "Real examples": 80})
    ws.append([])
    ws.append(["INFERRED from the corpus, not documented by the platform. Confirm with the TenderSure team."])
    ws.append(["L (Attachment) = upload with no compliance marks.  M (Compliance) = upload that carries compliance marks."])
    ws.append(["Select questions: the best option's score equals the row's marks (534 of 562 corpus rows)."])

    tiers = bank.tiers()
    entry_rows = []
    for tier in ("core", "common", "category"):
        for e in tiers.get(tier, []):
            entry_rows.append([
                e.canonical_text, tier, e.frequency, ", ".join(sorted(e.clients)),
                e.modal(e.sections), e.settled_answer_type, e.modal(e.mandatory),
                e.modal(e.upload_flag), e.modal(e.options), e.modal(e.archive_names),
                "yes" if e.conflicts else "", len(e.variants), e.key,
            ])
    _table(wb.create_sheet("Bank"),
           ["Question", "Tier", "Sheets", "Clients", "Section", "Answer type", "Mandatory",
            "Upload flag", "Options", "Archive name", "Conflict", "Wordings", "Key"],
           entry_rows, {"Question": 70, "Section": 28, "Options": 30, "Archive name": 24, "Key": 30})

    ws = wb.create_sheet("Conflicts")
    conf_rows = []
    for e in bank.conflicts():
        for c in e.conflicts:
            conf_rows.append([e.canonical_text, c.field_name,
                              ", ".join(f"{k}: {v}" for k, v in sorted(c.values.items(), key=lambda kv: -kv[1])),
                              c.majority, f"{c.confidence:.0%}", len(c.minority_locations),
                              ", ".join(c.minority_locations[:12]), "", "", e.key])
    _table(ws, ["Question", "Field", "Values seen", "Majority", "Agreement", "Outliers",
                "Outlier locations", "Decision", "Note", "Key"], conf_rows,
           {"Question": 60, "Values seen": 26, "Outlier locations": 50, "Decision": 14, "Note": 30, "Key": 30})
    for r in range(2, len(conf_rows) + 2):
        ws.cell(r, 8).fill = READ_FILL
    if conf_rows:
        dv = DataValidation(type="list", allow_blank=True,
                            formula1='"1,2,3,4,5,True,False,none,attachment,compliance"')
        ws.add_data_validation(dv)
        dv.add(f"H2:H{len(conf_rows) + 1}")
    wb.save(path)
    return path


def load_decisions(path: str) -> Dict[tuple, str]:
    """Read rulings from the Conflicts sheet of a bank workbook.

    Returns {(question key, field): value}. Blank decisions are skipped, so a
    partly filled sheet is fine.
    """
    ws = openpyxl.load_workbook(path, data_only=True)["Conflicts"]
    head = [c.value for c in ws[1]]
    i_key, i_field, i_dec = head.index("Key"), head.index("Field"), head.index("Decision")
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        dec = row[i_dec]
        if dec is None or str(dec).strip() == "":
            continue
        val = str(dec).strip()
        if isinstance(dec, bool):
            val = str(dec)
        elif isinstance(dec, float) and dec.is_integer():
            val = str(int(dec))
        out[(row[i_key], row[i_field])] = val
    return out


# --- generation QA -----------------------------------------------------------

def write_qa_report(plans: List[Plan], path: str, items=None):
    """`items`: decisions with answers attached (from review.apply_answers);
    computed fresh from the plans when absent."""
    from .review import decision_items, number_of
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Decisions"
    items = items if items is not None else decision_items(plans)
    _table(ws, ["ID", "What to decide", "Categories", "Proposed", "Your answer", "Status", "Why / options"],
           [[i.id, i.decide, ", ".join(number_of(c) for c in i.codes), i.proposed,
             i.answer, i.status or ("open" if i.type != "check" else "fix by hand"), i.why] for i in items],
           {"ID": 22, "What to decide": 70, "Categories": 24, "Proposed": 34, "Your answer": 26,
            "Status": 22, "Why / options": 60})
    for r in range(2, len(items) + 2):
        ws.cell(r, 5).fill = READ_FILL
        if str(ws.cell(r, 6).value).startswith(("NOT", "PROBLEM")):
            ws.cell(r, 6).font = Font(bold=True, color="FFC00000")
    ws.append([])
    for line in (
        "This tab is the whole review. Leave 'Your answer' blank to accept the proposal (including model proposals).",
        "Q- rows: 'remove all', 'remove: 3, 7', 'keep: 1, 2', 'reword: 9' (use the proposed wording) or "
        "'reword 9: your own wording'; combine with ';'. Rewording keeps the answer type and marks.",
        "A- rows: 'add all', 'add: 1, 3' or 'skip'. Added questions arrive with 0 marks; give the section marks on its M- row.",
        "N- rows: no source in the corpus; questions need writing (--model drafts them).",
        "M- rows: only the sections to change, e.g. 'technical 10, trade_reference 35'. Each sheet must still total 100.",
        "T- rows: decided once for the whole batch.",
        "Then run: python -m tendersure generate ... --review <this file>. Answers are applied and carried forward.",
        "The Detail tabs are optional background.",
    ):
        ws.append([line])

    # Model proposals, kept so applying the review replays exactly what was shown.
    hidden = wb.create_sheet("_proposals")
    hidden.append(["code", "proposal"])
    for p in plans:
        if p.proposal:
            hidden.append([p.target.code, json.dumps(p.proposal, ensure_ascii=False)])
    hidden.sheet_state = "hidden"

    ws = wb.create_sheet("Detail - overview")
    over = []
    for p in plans:
        qs = p.questions
        orig = Counter(q.origin for q in qs)
        errors = sum(1 for f in p.findings if f.severity == "error")
        over.append([
            p.target.code, p.target.description, p.target.group or "", p.skeleton.sheet.name,
            p.skeleton.sheet.description, f"{p.skeleton.score:.2f}", len(qs),
            sum(q.needs_reading for q in qs), orig[ADAPTED], orig[NEW], len(p.suggestions),
            errors, len(p.findings) - errors, p.total(), "; ".join(p.notes),
        ])
    _table(ws, ["Code", "Category", "Group", "Built from", "Built from (description)", "Match",
                "Questions", "To read", "Adapted", "New", "Suggestions", "Errors", "Warnings", "Total",
                "Notes"],
           over, {"Category": 50, "Built from (description)": 44, "Group": 24, "Code": 18, "Built from": 18,
                  "Notes": 50})
    ws.append([])
    ws.append(["To read = rows specific to the category the sheet was built from, plus inserted rows. "
               "They are grouped into the Q- rows of the Decisions tab."])

    rows = []
    for p in plans:
        for i, q in enumerate(p.questions, start=1):
            if not (q.needs_reading or q.origin != "reused unchanged"):
                continue
            rows.append([p.target.code, i, section_key(q.row.section), q.row.criteria, q.origin,
                         "yes" if q.needs_reading else "", q.row.marks, q.row.answer_type,
                         "; ".join(q.notes), q.source])
    ws = wb.create_sheet("Detail - rows")
    _table(ws, ["Code", "Q#", "Section", "Question", "Origin", "Read", "Marks", "Type", "Why", "Source"],
           rows, {"Question": 60, "Why": 60, "Code": 18, "Source": 22})
    for r in range(2, len(rows) + 2):
        if ws.cell(r, 6).value:
            for c in range(1, 11):
                ws.cell(r, c).fill = READ_FILL

    marks_rows = []
    for p in plans:
        sm = p.section_marks()
        for key, (m, sysm, comp) in sm.items():
            if not m and not p.marks_alternatives.get(key):
                continue
            alts = p.marks_alternatives.get(key, {})
            others = sorted({v for n, v in alts.items() if n != p.skeleton.sheet.name})
            marks_rows.append([p.target.code, key, m, sysm, comp, p.marks_source.get(key, ""),
                               ", ".join(f"{v:g}" for v in others)])
    _table(wb.create_sheet("Detail - marks"),
           ["Code", "Section", "Marks", "System", "Compliance", "Taken from", "Other close matches used"],
           marks_rows, {"Code": 18, "Taken from": 20, "Other close matches used": 26})
    wb["Detail - marks"].append([])
    wb["Detail - marks"].append(["Marks are the client's decision. Change them in the Decisions tab (M- rows)."])

    sug = []
    for p in plans:
        for s in p.suggestions:
            sug.append([p.target.code, s.section, s.row.criteria, s.row.marks, s.row.answer_type,
                        s.row.options or "", s.source, s.reason])
    _table(wb.create_sheet("Detail - suggestions"),
           ["Code", "Section", "Question", "Marks there", "Type", "Options", "Source", "Why suggested"],
           sug, {"Question": 60, "Why suggested": 50, "Code": 18, "Source": 20})

    _table(wb.create_sheet("Detail - findings"), ["Code", "Row", "Rule", "Class", "Severity", "Problem"],
           [[p.target.code, f.excel_row, f.rule, f.kind, f.severity, f.message]
            for p in plans for f in p.findings],
           {"Code": 18, "Rule": 22, "Problem": 70})
    wb.save(path)
    return path
