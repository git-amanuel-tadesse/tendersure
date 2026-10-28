"""Read TenderSure criteria workbooks into structured rows.

Deterministic. No model involved. One Row per question, plus the
structural subtotal/total rows kept separately for arithmetic checks.
"""

import re
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, List

import openpyxl

from .schema import COLUMNS, LAYOUT, STRUCTURAL_LABELS, CLIENT_TOKENS, COUNTRY_CODES, SECTION_KEYS


@dataclass
class Row:
    source_file: str
    sheet: str
    excel_row: int
    section: Optional[str] = None
    sub_section: Optional[str] = None
    criteria: str = ""
    marks: Optional[float] = None
    system: Optional[float] = None
    compliance: Optional[float] = None
    archive_short_name: Optional[str] = None
    acronym: Optional[str] = None
    answer_type: Optional[int] = None
    mandatory: bool = False
    attachment: bool = False
    compliance_attach: bool = False
    options: Optional[str] = None
    option_scores: Optional[str] = None
    gate_question: bool = False
    applies_to: Optional[str] = None
    eligibility: bool = False

    def option_list(self) -> List[str]:
        if not self.options:
            return []
        return [o.strip() for o in str(self.options).split(",") if o.strip()]

    def score_list(self) -> List[str]:
        if self.option_scores is None or str(self.option_scores).strip() == "":
            return []
        return [s.strip() for s in str(self.option_scores).split(",") if s.strip() != ""]

    def as_dict(self):
        return asdict(self)


@dataclass
class StructuralRow:
    sheet: str
    excel_row: int
    label: str
    marks: Optional[float]
    system: Optional[float]
    compliance: Optional[float]


@dataclass
class Sheet:
    source_file: str
    name: str
    title: str
    code: Optional[str]
    description: Optional[str]
    client: Optional[str]
    header_row: int
    n_columns: int
    rows: List[Row] = field(default_factory=list)
    structural: List[StructuralRow] = field(default_factory=list)
    source_path: Optional[str] = None
    misplaced_labels: List[int] = field(default_factory=list)
    source_hash: Optional[str] = None      # the workbook version in the bank


def _clean(v):
    if v is None:
        return None
    s = str(v).replace("\xa0", " ").strip()
    return s if s else None


def _num(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _flag(v):
    if v is None:
        return False
    return str(v).strip().lower() in {"true", "yes", "1", "y", "x"}


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def normalise(text: str) -> str:
    """Collapse a question to a comparable key, with client names templated."""
    s = str(text).replace("\xa0", " ").strip().lower()
    for tok in CLIENT_TOKENS:
        s = re.sub(rf"\b{tok.lower()}\b", "<client>", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w<>/ ]", "", s)
    return s.strip()


def section_key(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    low = " ".join(str(label).lower().split())
    for needle, key in SECTION_KEYS:
        if needle in low:
            return key
    return low


def find_header_row(ws, max_scan: int = 10) -> Optional[int]:
    for r in range(1, min(max_scan, ws.max_row) + 1):
        if str(ws.cell(r, COLUMNS["section"]).value).strip() == "Section":
            return r
    return None


def header_columns(ws, hdr: int) -> Dict[str, int]:
    """Field -> column, from the header row's labels. "Compliance" appears
    twice: the first is the marks column, the second the upload flag."""
    fields_by_label: Dict[str, List[str]] = {}
    for f, label in LAYOUT:
        fields_by_label.setdefault(label.lower(), []).append(f)
    cols: Dict[str, int] = {}
    last = COLUMNS["section"]
    for c in range(COLUMNS["section"], ws.max_column + 1):
        label = _clean(ws.cell(hdr, c).value)
        if not label:
            continue
        last = c
        f = next((f for f in fields_by_label.get(" ".join(label.lower().split()), []) if f not in cols), None)
        if f:
            cols[f] = c
    if not {"criteria", "marks", "system", "compliance"} <= cols.keys():
        # Unrecognised labels: assume the 14/16-column positions.
        return {f: c for f, c in COLUMNS.items() if c <= last}
    return cols


def split_title(title: str):
    """'ERCS-ET-2026-002: Supply of Electronics' -> (code, description, client)."""
    if not title:
        return None, None, None
    parts = str(title).split(":", 1)
    code = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else None
    client = None
    m = re.match(r"^([A-Za-z]+)", code)
    if m and m.group(1).upper() not in COUNTRY_CODES:
        client = m.group(1).upper()
    return code, desc, client


def parse_sheet(ws, source_file: str) -> Optional[Sheet]:
    hdr = find_header_row(ws)
    if hdr is None:
        return None

    title = _clean(ws.cell(2, COLUMNS["section"]).value) or ""
    code, desc, client = split_title(title)

    n_cols = 0
    for c in range(COLUMNS["section"], ws.max_column + 1):
        if ws.cell(hdr, c).value:
            n_cols = c - COLUMNS["section"] + 1
    cols = header_columns(ws, hdr)

    sheet = Sheet(
        source_file=source_file,
        name=ws.title,
        title=title,
        code=code,
        description=desc,
        client=client,
        header_row=hdr,
        n_columns=n_cols,
    )

    current_section = None
    current_sub = None

    for r in range(hdr + 1, ws.max_row + 1):
        def v(f):
            return ws.cell(r, cols[f]).value if f in cols else None

        sec = _clean(v("section"))
        sub = _clean(v("sub_section"))
        crit = _clean(v("criteria"))

        # The label belongs in B, but has been seen in D ("Sub Total" typed
        # into the Criteria column); either way the row is structural.
        label = next((v for v in (sec, sub, crit) if v and v.lower() in STRUCTURAL_LABELS), None)
        if label:
            if label != sec:
                sheet.misplaced_labels.append(r)
            sheet.structural.append(
                StructuralRow(
                    sheet=ws.title,
                    excel_row=r,
                    label=label,
                    marks=_num(v("marks")),
                    system=_num(v("system")),
                    compliance=_num(v("compliance")),
                )
            )
            continue

        # An unlabelled row carrying only marks in E/F/G is an orphan subtotal.
        if not crit and not sec:
            m = _num(v("marks"))
            s = _num(v("system"))
            g = _num(v("compliance"))
            if m is not None and (s is not None or g is not None):
                sheet.structural.append(
                    StructuralRow(ws.title, r, "<unlabelled>", m, s, g)
                )
            continue

        if sec:
            current_section = sec
            current_sub = None
        if sub:
            current_sub = sub
        if not crit:
            continue

        sheet.rows.append(
            Row(
                source_file=source_file,
                sheet=ws.title,
                excel_row=r,
                section=current_section,
                sub_section=current_sub,
                criteria=crit,
                marks=_num(v("marks")),
                system=_num(v("system")),
                compliance=_num(v("compliance")),
                archive_short_name=_clean(v("archive_short_name")),
                acronym=_clean(v("acronym")),
                answer_type=_int(v("answer_type")),
                mandatory=_flag(v("mandatory")),
                attachment=_flag(v("attachment")),
                compliance_attach=_flag(v("compliance_attach")),
                options=_clean(v("options")),
                option_scores=_clean(v("option_scores")),
                gate_question=_flag(v("gate_question")),
                applies_to=_clean(v("applies_to")),
                eligibility=_flag(v("eligibility")),
            )
        )

    return sheet


def parse_workbook(path: str) -> List[Sheet]:
    wb = openpyxl.load_workbook(path, data_only=True)
    name = path.split("/")[-1]
    out = []
    for sn in wb.sheetnames:
        s = parse_sheet(wb[sn], name)
        if s is not None:
            s.source_path = path
            out.append(s)
    return out
