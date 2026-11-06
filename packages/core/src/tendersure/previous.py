"""The client's previous category list (spec 18 D): the category names of the
client's last round, as evidence for refining this round's draft.

Two layouts are read:
- a draft-layout list (number, description, groups), read as a draft is;
- a results list: each category a header row above the suppliers who
  qualified in it. A header row's text starts with a category code
  ("G/001/2025-2026 / ..."), or the row is merged across the table from its
  first column. The code and a closing count "(17)" are dropped.

Only category names leave this module. Supplier rows hold names, emails and
phone numbers: they are never kept, logged or sent anywhere.
"""

import re
from typing import List, Optional, Sequence, Tuple

import openpyxl

from . import refine as rf

_CODE = re.compile(r"^\s*[A-Za-z]{1,6}\s*/\s*\d{1,4}(?:\s*/\s*[\d\s-]{2,12}?)?\s*[/:–-]\s+")
_COUNT = re.compile(r"\s*\(\s*\d+\s*\)\s*$")
_NO_CATEGORY = re.compile(r"\bno\s+categor(y|ies)\s+selected\b", re.I)
_COLUMN_HEAD = re.compile(r"^(supplier|suppliers|supplier name|company|company name|name|vendor|serial|s/?n|no\.?|#)$",
                          re.I)
_SCAN = 12            # columns a header's text may sit in


def _text(values) -> str:
    return next((" ".join(str(v).split()) for v in values if isinstance(v, str) and v.strip()), "")


def _results_names(ws) -> List[str]:
    """Header names of a results tab; [] when the tab is not laid out that way."""
    rows = list(ws.iter_rows(max_col=_SCAN, values_only=True))
    used = [i for r in rows for i, v in enumerate(r, 1) if v not in (None, "")]
    if not used:
        return []
    first = min(used)
    merged = {m.min_row for m in ws.merged_cells.ranges
              if m.min_row == m.max_row and m.min_col == first and m.max_col - m.min_col >= 2}
    # rows above the table's own column headers ("Supplier Name, Score...") are titles
    top = next((n for n, r in enumerate(rows, 1)
                if any(isinstance(v, str) and _COLUMN_HEAD.match(v.strip()) for v in r)), 0)
    names = []
    for n, r in enumerate(rows, 1):
        text = _text(r)
        if not text or n < top or _COLUMN_HEAD.match(text) or _NO_CATEGORY.search(text):
            continue
        code = _CODE.match(text)
        if code or (n in merged and not text.isdigit()):
            name = _COUNT.sub("", text[code.end():] if code else text).strip()
            if name:
                names.append(name)
    return names


def _draft_names(ws) -> List[str]:
    return [d.description for d in rf._read_tab(ws)]


def read_previous(path: str, sheets: Optional[Sequence[str]] = None, label: str = "") -> rf.Previous:
    """The previous list's category names, each once, in the file's order.
    `sheets`: the tabs to read; with none, every visible tab laid out as
    results, or else the first visible tab with a draft-layout list."""
    if path.lower().endswith(".csv"):
        names = [d.description for d in rf.read_draft(path)]
        return rf.Previous(_unique(names), label, path, [])
    wb = openpyxl.load_workbook(path, data_only=True)
    tabs = [s for s in sheets or [] if s]
    for t in tabs:
        if wb[t].sheet_state != "visible":
            raise ValueError(f"tab '{t}' is hidden")
    found: List[Tuple[str, List[str]]] = []
    if tabs:
        for t in tabs:
            names = _results_names(wb[t])
            found.append((t, names or _draft_names(wb[t])))
    else:
        visible = [ws for ws in wb.worksheets if ws.sheet_state == "visible"]
        found = [(ws.title, n) for ws in visible for n in [_results_names(ws)] if n]
        if not found:
            ws = next((ws for ws in visible if rf._has_header(ws)), None)
            if ws is None:
                raise ValueError("no category list found: neither results headers nor a numbered list")
            found = [(ws.title, _draft_names(ws))]
    names = _unique([n for _, ns in found for n in ns])
    if not names:
        raise ValueError("no category names found in the previous list")
    tab_of = {}
    for t, ns in found:                                 # each name's first tab (spec 19 B)
        for n in ns:
            tab_of.setdefault(_key(n), t)
    return rf.Previous(names, label, path, [t for t, _ in found], {n: tab_of[_key(n)] for n in names})


def _key(name: str) -> str:
    return " ".join(name.lower().split())


def _unique(names: List[str]) -> List[str]:
    seen = {}
    for n in names:
        seen.setdefault(_key(n), " ".join(n.split()))
    return list(seen.values())
