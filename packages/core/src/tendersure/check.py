"""Check a workbook after hand edits (spec 03, feedback item 6).

The team edits generated sheets and sends them on; editing can break things.
`check` reads a workbook - never writes to it - and lists every problem with
sheet, row, rule, severity, what was found and what was expected. Errors are
what the platform would reject or an agreed rule broken; warnings are likely
wrong and worth a look. The exit code is non-zero when any error is found,
so it can gate an upload.

Every sheet rule here is the same `check_` function generation applies (spec
02), so a rule cannot drift between the two flows. Rules on the workbook
itself (layout, codes, merged blocks, standard blocks, another client's name)
and the comparison with the generated version live in this module.
"""

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional

import openpyxl

from .categories import Category
from .parser import Row, Sheet, header_columns, normalise, parse_sheet, section_key
from .privacy import CLIENT_ALIASES
from .schema import LAYOUT, business_types
from .trust import context_key
from .validate import (PLATFORM, SILENT, SUB_CATEGORY, Finding, asks_for_document, check_gate, check_sub_category_question,
                       check_template, check_yes_no, validate_sheet)

KENYA_ONLY = ["evaluation", "eval_options", "eval_option_scores", "verify_against"]
_CODE = re.compile(r"^(?P<code>[A-Za-z]+[-_ ][\w-]*?(?P<num>\d{3}))\s*:\s*(?P<desc>.+)$")
_FIELDS = ["criteria", "marks", "system", "compliance", "answer_type", "mandatory", "attachment", "compliance_attach",
           "options", "option_scores", "archive_short_name", "acronym", "eligibility", "gate_question", "applies_to"]


def _f(sheet: str, row: Optional[int], rule: str, severity: str, message: str, fix: str = "",
       kind: str = SILENT) -> Finding:
    return Finding(sheet, row, rule, kind, severity, message, fix)


# --- the workbook itself (C1, C2, C5, C15) ---------------------------------------

def check_layout(ws, sheet: Sheet) -> List[Finding]:
    """C1: the 21-column template's headers, in order."""
    want = [label for _, label in LAYOUT]
    got = [str(ws.cell(sheet.header_row, 2 + i).value or "").strip() for i in range(len(want))]
    if got == want:
        return []
    wrong = next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)
    return [_f(sheet.name, sheet.header_row, "LAYOUT-HEADER", "error",
               f"Column {openpyxl.utils.get_column_letter(2 + wrong)} reads '{got[wrong]}', the template has "
               f"'{want[wrong]}'.", "Use the 21-column template's header row.", PLATFORM)]


def check_code(ws, sheet: Sheet, client: Optional[str]) -> List[Finding]:
    """C2: row 2 reads 'CODE: Description', the code ends in a three-digit
    category number and has the client's prefix, and names the tab."""
    m = _CODE.match(sheet.title or "")
    if not m:
        return [_f(sheet.name, 2, "CODE-FORMAT", "error", f"Row 2 reads '{sheet.title[:60]}'.",
                   "Write 'CODE: Description', the code ending in the 3-digit category number.", PLATFORM)]
    out, code = [], m.group("code")
    if client and not code.upper().startswith(client.upper()):
        out.append(_f(sheet.name, 2, "CODE-CLIENT", "error", f"Code {code} does not start with {client}.",
                      f"Use the {client} prefix."))
    if ws.title != code[:31] and not ws.title.startswith(code[:28] + "~"):
        out.append(_f(sheet.name, 2, "CODE-TAB", "error", f"The tab is named '{ws.title}' but row 2 says {code}.",
                      "Name the tab after the code."))
    return out


def check_kenya_columns(ws, sheet: Sheet) -> List[Finding]:
    """C5: Evaluation, Eval Options, Eval Options Scores and Verify Against
    belong to the Kenya template and stay empty."""
    cols = header_columns(ws, sheet.header_row)
    out = []
    for f in KENYA_ONLY:
        if f not in cols:
            continue
        rows = [r for r in range(sheet.header_row + 1, ws.max_row + 1) if ws.cell(r, cols[f]).value not in (None, "")]
        if rows:
            label = dict(LAYOUT)[f]
            out.append(_f(sheet.name, rows[0], "KENYA-COLUMN", "warning",
                          f"{label} has values on {len(rows)} row(s); the column is for the Kenya template.",
                          f"Leave {label} empty."))
    return out


def check_blocks(ws, sheet: Sheet) -> List[Finding]:
    """C15: a Sub Section is one merged block; a question row whose Sub
    Section cell is blank and not part of a merged block splits it."""
    cols = header_columns(ws, sheet.header_row)
    c = cols.get("sub_section")
    merged = {r for m in ws.merged_cells.ranges if m.min_col <= (c or 0) <= m.max_col
              for r in range(m.min_row, m.max_row + 1)}
    out = []
    for r in sheet.rows:
        prev = next((x for x in reversed(sheet.rows) if x.excel_row < r.excel_row), None)
        if c and r.sub_section and prev and prev.section == r.section and \
                not ws.cell(r.excel_row, c).value and r.excel_row not in merged:
            out.append(_f(sheet.name, r.excel_row, "BLOCK-SPLIT", "warning",
                          f"Row's Sub Section cell is empty and outside the '{r.sub_section}' block.",
                          "Merge it into the Sub Section block above."))
    return out


# --- the sheet's content (C17, C18) ------------------------------------------------

EVIDENCE = "an evidence upload"


def check_standard_blocks(sheet: Sheet) -> List[Finding]:
    """C17: Declaration and Submission present; every Trade Reference and
    shareholder block asks what its first block asks. Uploads count by what
    they are, not their wording: a client may word each block's evidence
    differently ("Attach evidence of project completion..." / "Attach
    evidence e.g. PO...")."""
    out = []
    keys = {section_key(r.section) for r in sheet.rows}
    for need in ("declaration", "submission"):
        if need not in keys:
            out.append(_f(sheet.name, None, "STD-SECTION", "error", f"No {need.title()} section.",
                          f"Restore the {need.title()} section."))
    for section in ("trade_reference", "shareholder"):
        blocks: Dict[str, List[str]] = defaultdict(list)
        first_row: Dict[str, int] = {}
        for r in sheet.rows:
            if section_key(r.section) == section and r.sub_section:
                blocks[r.sub_section].append(EVIDENCE if asks_for_document(r) else
                                             re.sub(r"\s*\d+", "#", normalise(r.criteria)))
                first_row.setdefault(r.sub_section, r.excel_row)
        names = list(blocks)
        for name in names[1:]:
            missing = [q for q in blocks[names[0]] if q not in blocks[name]]
            if missing:
                out.append(_f(sheet.name, first_row[name], "STD-BLOCK", "error",
                              f"'{name}' lacks what '{names[0]}' asks: {'; '.join(missing[:3])}.",
                              f"Restore the missing question(s) in '{name}'."))
    return out


def check_other_clients(sheet: Sheet, client: Optional[str]) -> List[Finding]:
    """C18: another client's name inside a question (copied from its sheet)."""
    own = (client or sheet.client or "").upper()
    names = [(c, a) for c, aliases in CLIENT_ALIASES.items() if c.upper() != own for a in aliases]
    out = []
    for r in sheet.rows:
        hit = next((a for _, a in names if re.search(rf"\b{re.escape(a)}\b", r.criteria)), None)
        if hit:
            out.append(_f(sheet.name, r.excel_row, "OTHER-CLIENT", "error",
                          f"The question names '{hit}', another client.",
                          f"Use {own or 'this client'}'s name."))
    return out


# --- against the categories table (C3, C11, C12, C14) ------------------------------

def check_category(sheet: Sheet, cat: Optional[Category]) -> List[Finding]:
    if cat is None:
        return [_f(sheet.name, 2, "CAT-UNKNOWN", "error", "No category in the categories table has this number.",
                   "Check the code, or add the category.")]
    out = []
    norm = lambda t: " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))
    if norm(sheet.description) != norm(cat.description):
        out.append(_f(sheet.name, 2, "CAT-DESCRIPTION", "warning",
                      f"Description differs from the categories table ('{cat.description[:60]}').",
                      "Use the categories table's description."))
    elig = " ".join(r.criteria.lower() for r in sheet.rows if section_key(r.section) == "eligibility")
    out += [_f(sheet.name, None, "ELIG-CATEGORY-DOC", "error", f"Eligibility does not ask for {doc}.",
               f"Add 'Please attach a valid {doc}' to the Eligibility section.") for doc in cat.eligibility
            if doc.lower() not in elig]
    out += check_sub_category_question(sheet, cat.sub_categories)
    out += check_gate(sheet, cat.gate_question, business_types(cat.applies_to))
    return out


def _number(sheet: Sheet) -> Optional[int]:
    m = _CODE.match(sheet.title or "")
    return int(m.group("num")) if m else None


# --- against the generated version (C16) --------------------------------------------

def check_against(sheet: Sheet, before: Optional[Sheet]) -> List[Finding]:
    """C16: what changed since the generated version - removed, added or
    changed rows - so an edit is visible without failing the check."""
    if before is None:
        return [_f(sheet.name, None, "EDIT-NEW-SHEET", "warning", "Not in the generated workbook.")]
    a, b = [normalise(r.criteria) for r in before.rows], [normalise(r.criteria) for r in sheet.rows]
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            out += [_f(sheet.name, None, "EDIT-REMOVED", "warning",
                       f"Removed since generation: '{before.rows[i].criteria[:70]}' (was row {before.rows[i].excel_row}).")
                    for i in range(i1, i2)]
        if op in ("insert", "replace"):
            out += [_f(sheet.name, sheet.rows[j].excel_row, "EDIT-ADDED", "warning",
                       f"Added since generation: '{sheet.rows[j].criteria[:70]}'.") for j in range(j1, j2)]
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                x, y = before.rows[i].as_dict(), sheet.rows[j].as_dict()
                changed = [f"{k} {x[k]!r} -> {y[k]!r}" for k in _FIELDS if x[k] != y[k]]
                if changed:
                    out.append(_f(sheet.name, sheet.rows[j].excel_row, "EDIT-CHANGED", "warning",
                                  "Changed since generation: " + "; ".join(changed)))
    return out


# --- the whole workbook ---------------------------------------------------------------

def sheet_findings(s: Sheet, client: Optional[str], cat: Optional[Category] = None, with_category: bool = False,
                   yes_is: Optional[Callable[[List[Row], int], Optional[str]]] = None) -> List[Finding]:
    """Every content rule for one sheet - the same list generation runs before
    it writes a sheet (spec 02 G11) and `check` runs on an edited one."""
    out = validate_sheet(s) + check_template(s) + check_standard_blocks(s) + check_other_clients(s, client)
    if yes_is:
        out += check_yes_no(s, yes_is)
    if with_category:
        out += check_category(s, cat)
    return out


def check_workbook(path: str, client: Optional[str] = None, categories: Optional[List[Category]] = None,
                   against: Optional[str] = None,
                   yes_is: Optional[Callable[[List[Row], int], Optional[str]]] = None) -> List[Finding]:
    wb = openpyxl.load_workbook(path, data_only=True)
    sheets = [(ws, parse_sheet(ws, path.split("/")[-1])) for ws in wb.worksheets]
    sheets = [(ws, s) for ws, s in sheets if s is not None]
    before = {}
    if against:
        before = {s.code: s for s in (parse_sheet(ws, against) for ws in openpyxl.load_workbook(
            against, data_only=True).worksheets) if s is not None}
    by_number = {c.number: c for c in categories or []}
    out = []
    codes = Counter(s.code for _, s in sheets)
    for ws, s in sheets:
        out += check_layout(ws, s) + check_code(ws, s, client) + check_kenya_columns(ws, s) + check_blocks(ws, s)
        if codes[s.code] > 1:
            out.append(_f(s.name, 2, "CODE-DUPLICATE", "error", f"{codes[s.code]} sheets use code {s.code}.",
                          "Give each category its own code.", PLATFORM))
        out += sheet_findings(s, client, by_number.get(_number(s)), categories is not None, yes_is)
        if against:
            out += check_against(s, before.get(s.code))
    if categories is not None:
        seen = {_number(s) for _, s in sheets}
        out += [_f("(workbook)", None, "CAT-NO-SHEET", "error",
                   f"Category {c.number} ({c.description[:60]}) has no sheet.", "Generate its sheet.")
                for c in categories if c.number not in seen]
    if against:
        here = {s.code for _, s in sheets}
        out += [_f(code, None, "EDIT-SHEET-REMOVED", "warning", "In the generated workbook but not here.")
                for code in before if code not in here]
    return sorted(out, key=lambda f: (f.sheet, f.excel_row or 0, f.rule))
