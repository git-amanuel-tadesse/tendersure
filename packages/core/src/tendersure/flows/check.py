"""The check (spec 03): a workbook after hand edits -> every problem, by sheet
and row. It never writes to the workbook it checks."""

import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from .. import categories as cat
from .. import trust
from .. import yesno
from ..paths import current
from .common import Model, log


@dataclass
class CheckResult:
    findings: list                      # validate.Finding
    out: str                            # the report workbook

    @property
    def errors(self) -> int:
        return sum(f.severity == "error" for f in self.findings)

    @property
    def warnings(self) -> int:
        return sum(f.severity == "warning" for f in self.findings)

    @property
    def per_sheet(self) -> Dict[str, Counter]:
        per = defaultdict(Counter)
        for f in self.findings:
            per[f.sheet][f.severity] += 1
        return dict(per)


def check(workbook: str, client: Optional[str] = None, categories: Optional[str] = None,
          against: Optional[str] = None, yes_no: Optional[str] = None, out: Optional[str] = None,
          model: Model = Model(), brief: str = "", client_rules: Optional[str] = None) -> CheckResult:
    from .. import check as ck
    from .. import client_rules as cr
    rules_file = client_rules or current().learned("client_rules")
    rules = cr.for_client(cr.load(rules_file), client) if client and os.path.exists(rules_file) else []
    cats = cat.load(categories) if categories else None
    yes_is = None
    book = yesno.load(yes_no or current().learned("yesno_polarity"))
    if book.verdicts:
        def yes_is(rows, i):
            v = book.lookup(trust.context_key(rows, i))
            return v.yes_is if v else None
    findings = ck.check_workbook(workbook, client, cats, against, yes_is, rules)
    if model.wanted()[0]:
        from .. import check_ai
        findings += check_ai.read(workbook, model.name, client=client, brief=brief)
    out = out or os.path.join(current().reports, os.path.splitext(os.path.basename(workbook))[0] + "_check.xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    write_report(findings, out)
    r = CheckResult(findings, out)
    for sheet, c in sorted(r.per_sheet.items()):
        if c["error"]:
            log.info(f"  {sheet}: {c['error']} errors, {c['warning']} warnings")
    log.info(f"check: {r.errors} errors, {r.warnings} warnings in {len(r.per_sheet)} sheets "
             f"({', '.join(f'{rule} {n}' for rule, n in Counter(f.rule for f in findings if f.severity == 'error').most_common(8))})"
             f" -> {out}")
    return r


def write_report(findings: List, path: str):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Findings"
    head = ["Sheet", "Row", "Rule", "Severity", "Class", "Problem", "Suggested fix"]
    ws.append(head)
    for f in findings:
        ws.append([f.sheet, f.excel_row, f.rule, f.severity, f.kind, f.message, f.suggestion])
    red = PatternFill("solid", fgColor="FFF4CCCC")
    for row in ws.iter_rows(min_row=2):
        if row[3].value == "error":
            row[3].fill = red
    for c, w in zip(ws[1], [20, 6, 20, 9, 9, 80, 60]):
        c.font = Font(bold=True)
        ws.column_dimensions[c.column_letter].width = w
    ws.freeze_panes = "A2"
    ws = wb.create_sheet("Summary")
    ws.append(["Sheet", "Errors", "Warnings"])
    per = defaultdict(Counter)
    for f in findings:
        per[f.sheet][f.severity] += 1
    for sheet, c in sorted(per.items()):
        ws.append([sheet, c["error"], c["warning"]])
    wb.save(path)


def suggest_from_edits(generated: str, edited: str, client: str, client_rules: Optional[str] = None) -> list:
    """Client rules an edited copy suggests (spec 16 C): changes repeated on several sheets, none an
    existing rule covers."""
    import openpyxl
    from .. import client_rules as cr, suggest_rules as sr
    from ..parser import parse_sheet
    read = lambda path: [s for s in (parse_sheet(ws, os.path.basename(path)) for ws in
                                     openpyxl.load_workbook(path, data_only=True).worksheets) if s is not None]
    before = {s.code: s for s in read(generated)}
    rules_file = client_rules or current().learned("client_rules")
    existing = cr.for_client(cr.load(rules_file), client) if os.path.exists(rules_file) else []
    return sr.new_only(sr.from_edits(before, read(edited), client), existing)


def preview_sheet(workbook: str, code: str):
    """(every sheet name, the sheet asked for - or the first when code is '' or 'first' - parsed), or
    None when there is no such sheet (spec 16 F)."""
    import openpyxl
    from ..parser import parse_sheet
    wb = openpyxl.load_workbook(workbook, data_only=True)
    names = wb.sheetnames
    name = code if code in names else (names[0] if code in ("", "first") and names else None)
    if name is None:
        return None
    return names, name, parse_sheet(wb[name], os.path.basename(workbook))
