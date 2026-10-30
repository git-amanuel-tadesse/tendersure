"""The rules that shape a build, as checks on a workbook (spec 15): an edited
copy is held to what the client asked for, not only to the template.

- R1 the client's rules (client_rules): a removed question back, a replaced
  term back, a reworded question changed, a section over its limit, a
  sub-section's marks moved - errors, with the rule's note as the reason.
- R2 the categories table's technical, HR and licence items: missing, or a
  technical item's own marks changed - errors; an item found only by its
  words (reworded) - a warning.
- R3 Applies To on a gated sheet wider than the built-in audience rules -
  warnings: the team may have meant it.
"""

import re
from typing import List, Optional

from .categories import Category
from .parser import Row, Sheet, section_key
from .schema import business_types
from .validate import Finding

NEVER = {"declaration", "submission"}
_SILENT = "silent"


def _f(sheet: Sheet, row: Optional[Row], rule: str, severity: str, message: str, fix: str = "") -> Finding:
    return Finding(sheet.name, row.excel_row if row is not None else None, rule, _SILENT, severity, message, fix)


def _norm(text) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _why(rule) -> str:
    return f" ({rule.note})" if rule.note else ""


# --- R1 the client's rules ------------------------------------------------------------

def check_client_rules(sheet: Sheet, cat: Optional[Category], rules: list) -> List[Finding]:
    group = cat.group if cat is not None else None
    mine = [r for r in rules if (r.in_group(group) if cat is not None else not r.groups.strip())]
    rows = [r for r in sheet.rows if section_key(r.section) not in NEVER]
    out = []
    for rule in mine:
        pat = rule.pattern()
        if rule.action == "remove":
            out += [_f(sheet, r, "RULE-REMOVE", "error", f"'{r.criteria[:70]}' is back; the client's rule removes "
                       f"questions containing '{rule.match}'{_why(rule)}.", "Remove the question.")
                    for r in rows if pat.search(r.criteria)]
        elif rule.action == "replace" and not pat.search(rule.value):
            out += [_f(sheet, r, "RULE-REPLACE", "error", f"'{pat.search(r.criteria).group(0)}' is used; the client's "
                       f"rule says '{rule.value}'{_why(rule)}.", f"Write '{rule.value}'.")
                    for r in rows if pat.search(r.criteria)]
        elif rule.action == "reword":
            out += [_f(sheet, r, "RULE-REWORD", "error", f"'{r.criteria[:70]}' should read the client's wording"
                       f"{_why(rule)}.", f"Write: {rule.value}")
                    for r in rows if pat.search(r.criteria) and _norm(r.criteria) != _norm(rule.value)]
        elif rule.action == "limit":
            key, n = section_key(rule.match), int(rule.value)
            if key == "technical" and cat is not None and cat.technical:
                continue                               # the category lists its own technical items (spec 12 R2)
            section = [r for r in rows if section_key(r.section) == key]
            if len(section) > n:
                out.append(_f(sheet, section[n], "RULE-LIMIT", "error", f"{len(section)} questions in '{section[0].section}'; "
                              f"the client's rule allows at most {n}{_why(rule)}.", "Remove the extra questions."))
        elif rule.action == "marks":
            sub = [r for r in rows if _norm(r.sub_section).replace(" ", "") == _norm(rule.match).replace(" ", "")]
            got = sum(r.marks or 0 for r in sub)
            if sub and abs(got - float(rule.value)) > 1e-6:
                out.append(_f(sheet, sub[0], "RULE-MARKS", "error", f"'{rule.match}' carries {got:g} marks; the client's "
                              f"rule says {float(rule.value):g}{_why(rule)}.",
                              f"Give '{rule.match}' {float(rule.value):g} marks and keep the sheet at 100."))
    return out


# --- R2 the categories table's items ---------------------------------------------------

def _words(text: str) -> set:
    from .requirements import _words as words
    return words(text)


def _find(rows: List[Row], want: str):
    """(row, exact) for the question asking `want`: worded as generation words it, else
    sharing most of its words; (None, False) when none does."""
    for r in rows:
        if _norm(r.criteria) == _norm(want):
            return r, True
    w = _words(want)
    for r in rows:
        if w and len(w & _words(r.criteria)) * 2 >= len(w):
            return r, False
    return None, False


def check_category_items(sheet: Sheet, cat: Optional[Category]) -> List[Finding]:
    from .requirements import _YEARS, GRADE_ITEM, GRADE_ROW, _role_text, _technical_items, _technical_text
    if cat is None:
        return []
    out = []
    in_section = lambda key: [r for r in sheet.rows if section_key(r.section) == key]
    tech = in_section("technical")
    for item, marks in _technical_items(cat.technical):
        want = _technical_text(item)
        r, exact = _find(tech, want)
        if r is None:
            out.append(_f(sheet, None, "CAT-TECHNICAL", "error", f"No Technical question asks the category's item "
                          f"'{item[:70]}'.", f"Add: {want}"))
            continue
        if not exact:
            out.append(_f(sheet, r, "CAT-TECHNICAL", "warning", f"'{r.criteria[:70]}' rewords the category's item "
                          f"'{want[:70]}'.", "Keep the client's wording unless the client agreed."))
        if marks is not None and abs((r.marks or 0) - marks) > 1e-6:
            out.append(_f(sheet, r, "CAT-TECHNICAL", "error", f"'{r.criteria[:50]}' carries {r.marks or 0:g} marks; the "
                          f"categories table gives it {marks:g}.", f"Give it {marks:g} marks and keep the sheet at 100."))
    hr = in_section("hr")
    for role in cat.hr:
        m = _YEARS.search(role)                      # the role's name, as _role_text words it
        name = _words(role[:m.start()] if m else role)
        if name and not any(name <= _words(r.criteria) for r in hr):
            out.append(_f(sheet, None, "CAT-HR", "error", f"No HR question asks for the category's role '{role}'.",
                          f"Add: {_role_text(role)}"))
    elig = in_section("eligibility")
    for item in cat.licence:
        if GRADE_ITEM.search(item):
            found = any(GRADE_ROW.search(r.criteria) for r in elig)
        else:
            found = any(item.lower() in r.criteria.lower() for r in elig) or _find(elig, item)[0] is not None
        if not found:
            out.append(_f(sheet, None, "CAT-LICENCE", "error", f"Eligibility does not ask for the category's licence "
                          f"'{item}'.", "Add it to the Eligibility section."))
    return out


# --- R3 Applies To on a gated sheet ----------------------------------------------------

def check_audiences(sheet: Sheet, cat: Optional[Category]) -> List[Finding]:
    from .gating import default_audience
    if cat is None or not cat.gate_question:
        return []
    accepted = business_types(cat.applies_to)
    out = []
    for r in sheet.rows:
        want = default_audience(r)
        if want is None or r.gate_question:
            continue
        want = [t for t in accepted if t in want]
        got = business_types(r.applies_to) if r.applies_to else accepted
        wider = [t for t in got if t not in want]
        if wider:
            out.append(_f(sheet, r, "AT-AUDIENCE", "warning", f"'{r.criteria[:50]}' is asked of {', '.join(wider)}; "
                          f"it is for {', '.join(want) or 'no accepted type'}.",
                          f"Applies To: {', '.join(want)}" if want else "Remove the question."))
    return out


def check_rules(sheet: Sheet, cat: Optional[Category], rules: list) -> List[Finding]:
    return check_client_rules(sheet, cat, rules) + check_category_items(sheet, cat) + check_audiences(sheet, cat)
