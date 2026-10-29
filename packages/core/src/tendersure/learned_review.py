"""The review of learned drafts (spec 04 L6): one workbook, one line per
group of rewordings, answered in a Decision column and applied with
`learn --review`.

    Names tab   ok | a corrected Short Name | "Short Name | ACR"
    Yes-No tab  ok | good | bad

A blank Decision leaves the line for later. An applied line becomes
`confirmed` for every question it covers, so it is never asked again.
"""

from collections import defaultdict
from typing import Dict, List, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from . import glossary as gl
from . import grouping
from . import yesno
from .naming import acronym_for

NAME_HEADERS = ["ID", "Question", "Also asked as", "Clients", "Short Name", "Acronym", "Source", "Decision", "Keys"]
YN_HEADERS = ["ID", "Question", "Also asked as", "Yes is", "Source", "Decision", "Keys"]
SOURCE = {gl.DRAFTED: "drafted", gl.RULE_DRAFTED: "rules (no model)", gl.SIMILAR: "a confirmed rewording"}
HEAD = PatternFill("solid", fgColor="FFC5D9F1")
ANSWER = PatternFill("solid", fgColor="FFFFF2CC")


def _lines(items: list, key_of, proposal_of) -> List[Tuple[tuple, list]]:
    """Drafts grouped by rewording group and proposal, most used first."""
    rep = grouping.group([key_of(x) for x in items])
    lines: Dict[tuple, list] = defaultdict(list)
    for x in items:
        lines[(rep[key_of(x)],) + proposal_of(x)].append(x)
    return list(lines.items())


def _sheet(wb, title: str, headers: List[str], rows: List[list], widths: List[float]):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c, w in zip(ws[1], widths):
        c.font, c.fill = Font(bold=True), HEAD
        ws.column_dimensions[c.column_letter].width = w
    for r in rows:
        ws.append(r)
    decision = headers.index("Decision") + 1
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[decision - 1].fill = ANSWER
    ws.column_dimensions[openpyxl.utils.get_column_letter(len(headers))].hidden = True   # Keys
    ws.freeze_panes = "B2"


def write_review(glossary: gl.Glossary, book: yesno.Book, path: str) -> Tuple[int, int]:
    """Every draft still waiting for the team. Returns (name lines, Yes/No lines)."""
    drafts = sorted((e for e in glossary.entries if e.status in SOURCE), key=lambda e: -e.uses)
    names = []
    for i, ((_, short, acronym, status), es) in enumerate(
            _lines(drafts, lambda e: e.question_key, lambda e: (e.short_name, e.acronym, e.status)), 1):
        examples = list(dict.fromkeys(e.example for e in es))
        names.append([f"N-{i}", examples[0], "\n".join(examples[1:4]),
                      ", ".join(sorted({e.client for e in es})), short, acronym, SOURCE[status], "",
                      "\n".join(f"{e.client}\t{e.question_key}" for e in es)])
    verdicts = [v for v in book.verdicts if v.status != yesno.CONFIRMED]
    yn = []
    for i, ((_, yes_is, status), vs) in enumerate(
            _lines(verdicts, lambda v: v.question_key, lambda v: (v.yes_is, v.status)), 1):
        examples = list(dict.fromkeys(v.example for v in vs))
        yn.append([f"Y-{i}", examples[0], "\n".join(examples[1:4]), yes_is,
                   SOURCE.get(status, status), "", "\n".join(v.question_key for v in vs)])
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    _sheet(wb, "Names", NAME_HEADERS, names, [7, 60, 45, 14, 30, 11, 20, 28, 10])
    _sheet(wb, "Yes-No", YN_HEADERS, yn, [7, 60, 45, 8, 20, 12, 10])
    wb.save(path)
    return len(names), len(yn)


def _apply_name(answer: str, entries: List[gl.Entry]) -> str:
    if answer.lower() != "ok":
        short, _, acronym = (part.strip() for part in answer.partition("|"))
        if not short:
            return "no Short Name given"
        acronym = acronym.upper() or acronym_for(short)
        for e in entries:
            e.short_name, e.acronym = short, acronym
    for e in entries:
        e.status = gl.CONFIRMED
    return ""


def _apply_verdict(answer: str, verdicts: List[yesno.Verdict]) -> str:
    a = answer.lower()
    if a not in ("ok", yesno.YES_GOOD, yesno.YES_BAD):
        return f"'{answer}' is not ok, good or bad"
    for v in verdicts:
        if a != "ok":
            v.yes_is = a
        v.status = yesno.CONFIRMED
    return ""


def apply_review(path: str, glossary: gl.Glossary, book: yesno.Book) -> Tuple[int, List[str]]:
    """Apply the answered lines. Returns (lines applied, problems)."""
    wb = openpyxl.load_workbook(path)
    applied, problems = 0, []
    for title, apply in (("Names", _apply_name), ("Yes-No", _apply_verdict)):
        if title not in wb.sheetnames:
            continue
        ws = wb[title]
        cols = {c.value: i for i, c in enumerate(ws[1])}
        for row in ws.iter_rows(min_row=2, values_only=True):
            answer = str(row[cols["Decision"]] or "").strip()
            if not answer:
                continue
            keys = [k for k in str(row[cols["Keys"]] or "").split("\n") if k]
            if title == "Names":
                targets = [glossary.exact(*k.split("\t", 1)) for k in keys]
            else:
                targets = [book.lookup(k) for k in keys]
            problem = apply(answer, [t for t in targets if t is not None])
            if problem:
                problems.append(f"{row[cols['ID']]}: {problem}")
            else:
                applied += 1
    return applied, problems
