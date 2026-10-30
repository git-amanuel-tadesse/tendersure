"""Gate Question and Applies To (spec 02 G7, feedback items 4 and 8).

The gate is the question asking the supplier's business type ("Organization
type": Sole Proprietor / Private Limited Company / Share Company /
Individual Consultant). For a category whose Gate Question is TRUE in the
categories table, that question's options become the category's Applies To
list, it is the only row with Gate Question TRUE, and every other question
says which of those types answers it: company name -> the company types,
"Full name (as per ID)" -> Individual Consultant, a question everyone
answers -> blank. Questions only individuals answer are added from past
sheets when the sheet lacks them. Without a gate, both columns stay empty,
Individual Consultant is not offered, and individual-only questions go.

Who answers what is learned from past sheets that fill Applies To (SNV's)
and kept as the "audience" table (learned_store), which the team may edit.
Where the table has no answer, built-in rules decide (spec 14 A): the
shareholder section is for PLCs and share companies, the company's own
documents for the firms, the rest for everyone.
"""

import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import grouping
from .generate import (ADAPTED, Plan, PlannedRow, _apply_corrections, drop_empty_sections, drop_row, insert_rows,
                       keep_section_totals, rebalance_to_100)
from .learned_store import resolve
from .parser import Row, Sheet, section_key
from .schema import BUSINESS_TYPES, FIRMS, INDIVIDUAL, business_types
from .trust import context_key

AUDIENCE = "audience"
SEEDED, CONFIRMED = "seeded", "confirmed"
COLUMNS = ["question_key", "example", "applies_to", "status", "uses"]
COMPANIES = ["Private Limited Company", "Share Company"]
_COMPANIES_ROW = re.compile(r"private\s+limited|share\s+compan|limited\s+liability", re.I)
_FIRMS_ROW = re.compile(r"\b(company|firm|organi[sz]ation|business)(\s*'s)?\s+name\b|name\s+of\s+(the\s+)?(company|firm|"
                        r"organi[sz]ation|business)|\boffices?\b|(commercial|company|business)(\s*/\s*\w+)?\s+registration|"
                        r"incorporation|memorandum|articles\s+of"
                        r"\s+association|\bvat\b|company\s+profile", re.I)
_FULL_NAME = re.compile(r"\bfull\s+name\b", re.I)
_BUSINESS_QUESTION = re.compile(r"organi[sz]ation\s+type|type\s+of\s+organi[sz]ation|legal\s+(?:form|status)", re.I)


@dataclass
class Audience:
    question_key: str
    example: str
    applies_to: List[str]
    status: str = SEEDED
    uses: int = 0


def seed(sheets: List[Sheet]) -> List[Audience]:
    """For each question past sheets give an Applies To, the set they use most."""
    seen: Dict[str, Counter] = defaultdict(Counter)
    example: Dict[str, str] = {}
    for s in sheets:
        for i, r in enumerate(s.rows):
            types = business_types(r.applies_to) if r.applies_to else []
            if types and not r.gate_question:
                key = context_key(s.rows, i)
                seen[key][tuple(types)] += 1
                example.setdefault(key, r.criteria)
    return [Audience(k, example[k], list(c.most_common(1)[0][0]), SEEDED, sum(c.values())) for k, c in seen.items()]


class Book:
    def __init__(self, entries: List[Audience]):
        self.entries = entries
        self._by = {a.question_key: a for a in entries}
        self._rep: Optional[Dict[str, str]] = None

    def lookup(self, key: str) -> Tuple[Optional[Audience], bool]:
        """The question's audience, or a rewording's (similar=True)."""
        if key in self._by:
            return self._by[key], False
        known = list(self._by)
        rep = grouping.group(known + [key], apart=lambda a, b: a in self._by and b in self._by)
        twin = next((k for k in known if rep[k] == rep[key]), None)
        return (self._by[twin], True) if twin else (None, False)

    def individual_only(self) -> List[Audience]:
        return [a for a in self.entries if a.applies_to == [INDIVIDUAL]]


def reseed(existing: List[Audience], fresh: List[Audience]) -> List[Audience]:
    kept = [a for a in existing if a.status != SEEDED]
    taken = {a.question_key for a in kept}
    return kept + [a for a in fresh if a.question_key not in taken]


def load(target) -> List[Audience]:
    store, name = resolve(target, AUDIENCE)
    return [Audience(d["question_key"], d["example"], business_types(d["applies_to"]), d["status"] or SEEDED,
                     int(d["uses"] or 0)) for d in store.read(name)]


def save(entries: List[Audience], target):
    store, name = resolve(target, AUDIENCE)
    store.write(name, COLUMNS, [{"question_key": a.question_key, "example": a.example,
                                 "applies_to": ", ".join(a.applies_to), "status": a.status, "uses": a.uses}
                                for a in sorted(entries, key=lambda a: (", ".join(a.applies_to), a.question_key))])


def donors(sheets: List[Sheet], book: Book) -> Dict[str, Row]:
    """A past row for each question in the audience table, to copy from."""
    out: Dict[str, Row] = {}
    for s in sheets:
        for i, r in enumerate(s.rows):
            key = context_key(s.rows, i)
            if key in book._by and key not in out:
                out[key] = r
    return out


def business_question(plan: Plan) -> Optional[PlannedRow]:
    """The question asking the supplier's business type - not ERCS's "Type of
    Business" (Manufacturer / Trader...), which is what the supplier does."""
    for p in plan.questions:
        if _BUSINESS_QUESTION.search(p.row.criteria) or \
                {t for t in business_types(p.row.options) if t in BUSINESS_TYPES} >= {"Sole Proprietor", "Share Company"}:
            return p
    return None


def _keys(plan: Plan) -> List[Tuple[PlannedRow, str]]:
    qs = plan.questions
    rows = [p.row for p in qs]
    return [(p, context_key(rows, i)) for i, p in enumerate(qs)]


def _key_of(plan: Plan, p: PlannedRow) -> str:
    """The question's key in context, found by identity: two shareholders'
    "Name" rows are equal but are different rows."""
    rows = [q.row for q in plan.questions]
    return context_key(rows, next(j for j, r in enumerate(rows) if r is p.row))


def _without_gate(plan: Plan, book: Book, gate: Optional[PlannedRow]):
    for p in plan.questions:
        if p.row.gate_question or p.row.applies_to:
            p.note("Gate Question / Applies To cleared: the category has no gate", adapted=False)
        p.row.gate_question, p.row.applies_to = False, None
    if gate is not None and INDIVIDUAL in business_types(gate.row.options):
        opts, scores = gate.row.option_list(), gate.row.score_list()
        keep = [i for i, o in enumerate(opts) if business_types([o]) != [INDIVIDUAL]]
        gate.row.options = ", ".join(opts[i] for i in keep)
        gate.row.option_scores = ",".join(scores[i] for i in keep) if len(scores) == len(opts) else gate.row.option_scores
        gate.note("Individual Consultant removed from the options: the category does not accept individuals")
    only = {a.question_key for a in book.individual_only()}
    for i in reversed(range(len(plan.rows))):
        if plan.rows[i].kind == "question" and _key_of(plan, plan.rows[i]) in only:
            drop_row(plan, i, "asked only of individual consultants; this category does not accept them")


def _add_individual_questions(plan: Plan, book: Book, donor_rows: Dict[str, Row]):
    present = [k for _, k in _keys(plan)]
    for a in book.individual_only():
        if a.question_key in present or a.question_key not in donor_rows:
            continue
        rep = grouping.group(present + [a.question_key])
        if any(rep[k] == rep[a.question_key] for k in present):
            continue
        row = deepcopy(donor_rows[a.question_key])
        row.gate_question, row.applies_to = False, INDIVIDUAL
        p = PlannedRow(kind="question", row=row)
        _apply_corrections(p, {})
        insert_rows(plan, section_key(row.section) or "company", [p.row], ADAPTED, f"{row.sheet}!r{row.excel_row}",
                    "asked of individual consultants (the category accepts them)")
        for q in plan.questions:
            if q.row is p.row:
                q.notes += p.notes
                q.needs_reading = False


def default_audience(r: Row) -> Optional[List[str]]:
    """Who answers a question the audience table does not know (spec 14 A); None is everyone."""
    if section_key(r.section) == "shareholder" or _COMPANIES_ROW.search(r.criteria):
        return COMPANIES
    if _FIRMS_ROW.search(r.criteria):
        return FIRMS
    return None


def _full_name_text(country: str) -> str:
    return "Full name (as per Fayda ID)" if country == "ET" else "Full name (as per national ID or passport)"


def _add_individual_name(plan: Plan):
    """A gated sheet that accepts individuals asks their name, after the company name (spec 14 A)."""
    if any(_FULL_NAME.search(p.row.criteria) for p in plan.questions):
        return
    company = [i for i, p in enumerate(plan.rows) if p.kind == "question" and section_key(p.row.section) == "company"]
    if not company:
        return
    at = next((i for i in company if _FIRMS_ROW.search(plan.rows[i].row.criteria)
               and "name" in plan.rows[i].row.criteria.lower()), company[0])
    anchor = plan.rows[at].row
    row = Row(source_file="", sheet="", excel_row=0, section=anchor.section, sub_section=anchor.sub_section,
              criteria=_full_name_text(plan.target.country), marks=0.0, answer_type=1, mandatory=True,
              applies_to=INDIVIDUAL)
    plan.rows.insert(at + 1, PlannedRow(kind="question", row=row, origin=ADAPTED, source="standard row",
                                        notes=["the individual's name: the category accepts individual consultants"]))


def apply(plan: Plan, book: Book, donor_rows: Dict[str, Row]):
    """Gate Question and Applies To for one sheet, from its category's columns."""
    t, gate = plan.target, business_question(plan)
    if not t.gate_question:
        _without_gate(plan, book, gate)
        return
    types = business_types(t.applies_to)
    if gate is None:
        row = Row(source_file="", sheet="", excel_row=0, criteria="Organization type", answer_type=2, marks=0.0)
        insert_rows(plan, "company", [row], ADAPTED, "standard row", "business-type question: the category is gated")
        gate = next(p for p in plan.questions if p.row is row)
    g = gate.row
    options = ", ".join(types)
    if (g.options, g.gate_question) != (options, True):
        gate.note(f"gate question: options set to the category's business types ({options})")
    g.answer_type, g.mandatory, g.options, g.option_scores = 2, True, options, ",".join("0" for _ in types)
    g.gate_question, g.applies_to = True, None
    _add_individual_questions(plan, book, donor_rows)
    if INDIVIDUAL in types:
        _add_individual_name(plan)
    before, dropped = {k: v[0] for k, v in plan.section_marks().items()}, len(plan.dropped)
    unknown_documents = 0
    for i in reversed(range(len(plan.rows))):
        p = plan.rows[i]
        if p.kind != "question" or p is gate:
            continue
        p.row.gate_question = False
        if p.row.applies_to and business_types(p.row.applies_to) == [INDIVIDUAL] and p.source == "standard row":
            continue                                     # the individual's name, just added
        aud, similar = book.lookup(_key_of(plan, p))
        who = aud.applies_to if aud is not None else default_audience(p.row)
        if who is None:
            p.row.applies_to = None
            if p.row.answer_type == 5:
                unknown_documents += 1
                p.note("asked of every business type: no past sheet says who answers it", adapted=False)
            continue
        answer = [x for x in types if x in who]
        if not answer:
            drop_row(plan, i, f"answered only by {', '.join(who)}, which this category does not accept")
            continue
        p.row.applies_to = None if answer == types else ", ".join(answer)
        if aud is None and p.row.applies_to:
            p.note(f"Applies To: {', '.join(answer)} (built-in rule: no past sheet says who answers it)",
                   adapted=False)
        elif similar and p.row.applies_to:
            p.note(f"Applies To from a similar question ('{aud.example}')", adapted=False)
    if len(plan.dropped) > dropped:            # marks of rows no accepted type answers stay in the sheet
        drop_empty_sections(plan)
        here = {k for k, v in plan.section_marks().items()}
        problems = keep_section_totals(plan, {k: m for k, m in before.items() if k in here}, "Applies To")
        if problems or abs(plan.total() - 100) > 1e-6:
            rebalance_to_100(plan, "questions no accepted business type answers")
    if unknown_documents:
        plan.notes.append(f"{unknown_documents} uploads have no known audience and are asked of every business "
                          f"type: set them in data/audience.csv if some types should not answer")
