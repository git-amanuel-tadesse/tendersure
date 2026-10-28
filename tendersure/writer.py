"""Write criteria-sheet plans to Excel in the clients' existing format.

Layout and styling come from the skeleton's own workbook: column widths,
fonts, fills, borders, the merged 'Marks' banner, merged Section/Sub Section
blocks and merged Sub Total labels. The output should be indistinguishable
from a hand-made sheet.

Subtotal and total rows are written as literal numbers by default. The source
sheets use =SUM() formulas, but a file written here has no cached formula
results, and if the platform reads cell values without recalculating, a
formula would arrive blank. Pass formulas=True once the team confirms the
importer recalculates.
"""

from copy import copy
from typing import Dict, List, Optional

import openpyxl
from openpyxl.utils import get_column_letter

from .generate import Plan, check_total
from .parser import section_key
from .schema import LAYOUT

FIRST_COL = 2           # B
COL = {f: FIRST_COL + i for i, (f, _) in enumerate(LAYOUT)}
LAST_COL = FIRST_COL + len(LAYOUT) - 1
MARK_FIELDS = ("marks", "system", "compliance")     # summed on Sub Total and Total rows


def _num(v):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else v


def _copy_style(src, dst):
    if src is None or not src.has_style:
        return
    dst.font = copy(src.font)
    dst.fill = copy(src.fill)
    dst.border = copy(src.border)
    dst.alignment = copy(src.alignment)
    dst.number_format = src.number_format
    dst.protection = copy(src.protection)


class _Styles:
    """Representative template cells for each role, per column."""

    def __init__(self, ws, plan: Plan):
        sk = plan.skeleton.sheet
        self.ws = ws
        self.header_row = sk.header_row
        first_q = sk.rows[0].excel_row if sk.rows else sk.header_row + 1
        sub = next((s for s in sk.structural if s.label.lower().startswith("sub")), None)
        tot = next((s for s in sk.structural if "total marks" in s.label.lower()), None)
        self.rows = {
            "title": 2, "banner": 3, "header": sk.header_row,
            "question": first_q,
            "subtotal": sub.excel_row if sub else None,
            "total": tot.excel_row if tot else None,
        }
        # A plain (non-first) question row for D..Q avoids a one-off fill.
        plain = next((r.excel_row for r in sk.rows[1:] if r.section == sk.rows[0].section), first_q)
        self.rows["plain"] = plain
        self.n_template_cols = sk.n_columns

    def cell(self, role: str, col: int):
        r = self.rows.get(role)
        if r is None:
            return None
        if role == "header" and col >= FIRST_COL + self.n_template_cols:
            col = FIRST_COL + 12          # borrow the Options header style (N)
        return self.ws.cell(r, col)


class Writer:
    def __init__(self, formulas: bool = False, template_loader=None):
        """`template_loader(sheet)` returns the workbook a sheet came from (the
        bank keeps them); by default it is read from the sheet's source path."""
        self.formulas = formulas
        self.template_loader = template_loader
        self._templates: Dict[str, openpyxl.Workbook] = {}

    def _template(self, sheet) -> openpyxl.Workbook:
        key = getattr(sheet, "source_hash", None) or sheet.source_path
        if key not in self._templates:
            self._templates[key] = self.template_loader(sheet) if self.template_loader \
                else openpyxl.load_workbook(sheet.source_path)
        return self._templates[key]

    def write(self, plans: List[Plan], out_path: str, enforce_total: bool = True):
        if enforce_total:
            for p in plans:
                check_total(p)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for i, plan in enumerate(plans):
            tpl_wb = self._template(plan.skeleton.sheet)
            if i == 0 and tpl_wb.loaded_theme:
                wb.loaded_theme = tpl_wb.loaded_theme
            self._write_sheet(wb, plan, tpl_wb[plan.skeleton.sheet.name])
        wb.save(out_path)
        return out_path

    def _write_sheet(self, wb, plan: Plan, tpl_ws):
        st = _Styles(tpl_ws, plan)
        title = _sheet_title(plan.target.code, wb.sheetnames)
        ws = wb.create_sheet(title)
        last_col = LAST_COL

        for col in range(1, max(last_col, tpl_ws.max_column) + 1):
            letter = get_column_letter(col)
            if letter in tpl_ws.column_dimensions:
                ws.column_dimensions[letter].width = tpl_ws.column_dimensions[letter].width
        if len(LAYOUT) > st.n_template_cols:
            for col in range(FIRST_COL + st.n_template_cols, last_col + 1):
                ws.column_dimensions[get_column_letter(col)].width = 18
        for r in range(1, st.header_row + 1):
            h = tpl_ws.row_dimensions[r].height
            if h:
                ws.row_dimensions[r].height = h
        ws.sheet_view.zoomScale = tpl_ws.sheet_view.zoomScale
        ws.sheet_view.showGridLines = tpl_ws.sheet_view.showGridLines

        c = ws.cell(2, FIRST_COL, f"{plan.target.code}: {plan.target.description}")
        _copy_style(st.cell("title", FIRST_COL), c)
        # The 'Marks' banner spans Marks to Evaluation, as in the template.
        for col in range(COL["marks"], COL["evaluation"] + 1):
            _copy_style(st.cell("banner", col), ws.cell(3, col))
        ws.cell(3, COL["marks"], "Marks")
        ws.merge_cells(start_row=3, start_column=COL["marks"], end_row=3, end_column=COL["evaluation"])

        hdr = st.header_row
        for (_, label), col in zip(LAYOUT, range(FIRST_COL, last_col + 1)):
            c = ws.cell(hdr, col, label)
            _copy_style(st.cell("header", col), c)

        self._write_body(ws, plan, st, hdr + 1, last_col)

    def _write_body(self, ws, plan: Plan, st: _Styles, start: int, last_col: int):
        r = start
        group_start = start            # first row since the last subtotal
        subtotal_rows: List[int] = []
        loose: List[int] = []          # question rows not covered by a subtotal yet
        blocks_b: List[List[int]] = []  # [first,last] runs of one section
        blocks_c: List[List[int]] = []
        prev_section = prev_sub = None

        for p in plan.rows:
            if p.kind == "question":
                q = p.row
                role_b = "question"
                new_section = p.section_label is not None or q.section != prev_section
                new_sub = new_section or q.sub_section != prev_sub
                fields = {
                    "section": p.section_label if new_section else None,
                    "sub_section": q.sub_section if new_sub else None,
                    "criteria": q.criteria,
                    "marks": _num(q.marks),
                    "eligibility": True if q.eligibility else None,
                    "system": _num(q.system), "compliance": _num(q.compliance),
                    "archive_short_name": q.archive_short_name, "acronym": q.acronym,
                    "answer_type": q.answer_type,
                    "mandatory": True if q.mandatory else None,
                    "attachment": True if q.attachment else None,
                    "compliance_attach": True if q.compliance_attach else None,
                    "options": q.options, "option_scores": q.option_scores,
                    "gate_question": True if q.gate_question else None,
                    "applies_to": q.applies_to,
                }
                values = {COL[f]: v for f, v in fields.items()}
                for col in range(FIRST_COL, last_col + 1):
                    c = ws.cell(r, col, values.get(col))
                    role = role_b if col <= COL["sub_section"] else "plain"
                    _copy_style(st.cell(role, col), c)
                if new_section or not blocks_b:
                    blocks_b.append([r, r])
                else:
                    blocks_b[-1][1] = r
                if new_sub or not blocks_c:
                    blocks_c.append([r, r])
                else:
                    blocks_c[-1][1] = r
                prev_section, prev_sub = q.section, q.sub_section
                loose.append(r)
            else:
                is_total = p.kind == "total"
                role = "total" if is_total else "subtotal"
                ws.cell(r, FIRST_COL, "Total Marks" if is_total else "Sub Total")
                for col in range(FIRST_COL, last_col + 1):
                    _copy_style(st.cell(role, col), ws.cell(r, col))
                for col in (COL[f] for f in MARK_FIELDS):
                    L = get_column_letter(col)
                    if is_total:
                        refs = subtotal_rows + loose
                        val = ("=" + "+".join(f"{L}{x}" for x in refs)) if self.formulas and refs \
                            else _num(sum(_val(ws, x, col) for x in refs))
                    else:
                        val = f"=SUM({L}{group_start}:{L}{r - 1})" if self.formulas \
                            else _num(sum(_val(ws, x, col) for x in loose))
                    ws.cell(r, col, val)
                ws.merge_cells(start_row=r, start_column=COL["section"], end_row=r, end_column=COL["criteria"])
                if not is_total:
                    subtotal_rows.append(r)
                    loose = []
                group_start = r + 1
                prev_section = prev_sub = None
            r += 1

        for col, blocks in ((COL["section"], blocks_b), (COL["sub_section"], blocks_c)):
            for a, b in blocks:
                if b > a:
                    ws.merge_cells(start_row=a, start_column=col, end_row=b, end_column=col)


def _val(ws, row, col) -> float:
    v = ws.cell(row, col).value
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _sheet_title(code: str, existing: List[str]) -> str:
    t = "".join(ch for ch in code if ch not in '[]:*?/\\')[:31]
    base, n = t, 2
    while t in existing:
        t = f"{base[:28]}~{n}"
        n += 1
    return t
