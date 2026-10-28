"""Category description in, criteria-sheet plan out.

The model selects; the code assembles. This module is the assembly half and
a deterministic default for the selection half:

  1. Retrieve the most similar past categories (requirement level).
  2. Take the best match from the target client as the skeleton - it carries
     the client's own conventions (declaration wording, 14 vs 16 columns,
     L vs M usage, where subtotals sit).
  3. Copy every row with the fields it already carries. Swap client names,
     apply human conflict rulings, and correct the unambiguous silent errors
     the validator knows how to fix. Every change is recorded on the row.
  4. Flag rows that are specific to the skeleton's category, so the reviewer
     reads those properly instead of all ~100.
  5. Offer category-specific rows from the closest text match (any client) as
     suggestions. They are not inserted unless a selector chooses them.
  6. Keep the skeleton's marks unless the reviewer supplies a split; check
     the result totals 100.

An optional selector (see `selectors.py`) may drop flagged rows, insert
suggested rows and draft new ones. It never sets answer types, options,
scores or upload flags on rows that came from the bank.
"""

import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import Row, Sheet, StructuralRow, normalise, section_key
from .schema import CLIENT_NAMES, LAYOUT, SECTION_KEYS, TOTAL_MARKS
from .similarity import Match, Retriever
from .trust import CLEAN, CORRECTABLE, FIELDS, Conventions, Grade, context_key, usable
from .validate import Finding, fix_marks_columns, validate_sheet

REUSED = "reused unchanged"
ADAPTED = "adapted"
NEW = "newly drafted"

# Rows seen in fewer than this share of the skeleton client's sheets are
# treated as specific to the skeleton's category.
CATEGORY_SPECIFIC_SHARE = 0.5
# A donor must be at least this similar on text to be worth suggesting from.
DONOR_MIN_TEXT = 0.2
# Sections whose content is expected to change with the category, and
# sections that never do (their rows are never suggested).
VARIABLE_SECTIONS = {"technical", "sla", "hr", "experience"}
GENERIC_SECTIONS = {"declaration", "submission", "shareholder", "trade_reference"}
MAX_DONORS = 3
MAX_SUGGESTIONS = 15


@dataclass
class Target:
    code: str
    description: str
    client: str
    group: Optional[str] = None
    client_name: Optional[str] = None   # how the client is named in text; defaults to `client`
    sub_categories: List[str] = field(default_factory=list)
    requirements: str = ""          # the client's stated requirements for this category, if any

    @property
    def name_in_text(self) -> str:
        return self.client_name or self.client


@dataclass
class PlannedRow:
    kind: str                       # question | subtotal | total
    row: Optional[Row] = None       # for questions
    origin: str = REUSED
    source: str = ""                # sheet!row it came from
    needs_reading: bool = False
    notes: List[str] = field(default_factory=list)
    section_label: Optional[str] = None   # label written in column B (first row of a section)
    ctx_key: str = ""               # question key in context (trust.context_key)
    client: Optional[str] = None    # client whose sheet the row was copied from
    contested: List[Tuple[str, dict]] = field(default_factory=list)  # (field, values seen) without a convention
    weight_hint: float = 0.0        # donor marks for an inserted row; used when a split is applied
    scores_hint: Optional[str] = None  # donor option scores for an inserted row

    def note(self, msg: str, adapted: bool = True):
        self.notes.append(msg)
        if adapted and self.origin == REUSED:
            self.origin = ADAPTED


@dataclass
class Suggestion:
    section: str
    row: Row
    source: str
    reason: str
    text_score: float = 0.0         # how closely the donor's description matches the target


@dataclass
class Plan:
    target: Target
    skeleton: Match
    matches: List[Match]
    rows: List[PlannedRow]
    n_columns: int
    suggestions: List[Suggestion] = field(default_factory=list)
    marks_source: Dict[str, str] = field(default_factory=dict)
    marks_alternatives: Dict[str, Dict[str, float]] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    dropped: List[Tuple[PlannedRow, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    same_category: bool = False     # built from the client's own sheet for this category
    weak_match: bool = False        # the source shares little with this category (kind/client only)
    proposal: Optional[dict] = None  # model proposal (selectors.py), applied only through review

    @property
    def questions(self) -> List[PlannedRow]:
        return [p for p in self.rows if p.kind == "question"]

    def section_marks(self) -> Dict[str, Tuple[float, float, float]]:
        out: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
        for p in self.questions:
            k = section_key(p.row.section)
            out[k][0] += p.row.marks or 0
            out[k][1] += p.row.system or 0
            out[k][2] += p.row.compliance or 0
        return {k: tuple(v) for k, v in out.items()}

    def total(self) -> float:
        return sum(p.row.marks or 0 for p in self.questions)


# --- text adaptation ----------------------------------------------------------

def swap_client(text: str, target_name: str) -> str:
    for name in CLIENT_NAMES:
        if name != target_name:
            text = re.sub(rf"\b{re.escape(name)}\b", target_name, text)
    return text


def fill_placeholder(text: str, target_name: str) -> str:
    """'Are you a current consultant of ?' -> '... of SNV?'"""
    return re.sub(r"\bof\s+\?", f"of {target_name}?", text)


# --- unambiguous corrections ----------------------------------------------------

def _apply_corrections(p: PlannedRow, decisions: Dict[Tuple[str, str], str]):
    """Fix silent errors whose correction is not a judgement call.

    Human rulings from the conflict file win over everything else. The rest
    mirror validator rules that have exactly one sensible fix; rules that need
    judgement (which of marks or scores is wrong, say) are left for review.
    """
    r = p.row
    key = normalise(r.criteria)

    at = decisions.get((key, "answer_type"))
    if at and str(r.answer_type) != at:
        p.note(f"answer type {r.answer_type} -> {at} (conflict ruling)")
        r.answer_type = int(at)
    mand = decisions.get((key, "mandatory"))
    if mand and str(r.mandatory) != mand:
        p.note(f"mandatory {r.mandatory} -> {mand} (conflict ruling)")
        r.mandatory = mand == "True"
    up = decisions.get((key, "upload_flag"))
    if up:
        new = (up in ("attachment", "both"), up in ("compliance", "both"))
        if (r.attachment, r.compliance_attach) != new:
            p.note(f"upload flag -> {up} (conflict ruling)")
            r.attachment, r.compliance_attach = new

    low = r.criteria.lower()
    asks_upload = low.startswith(("attach", "upload", "please attach", "please upload"))
    if asks_upload and r.answer_type != 5 and not r.option_list():
        p.note(f"answer type {r.answer_type} -> 5: the question asks for a document")
        r.answer_type = 5
        if not (r.attachment or r.compliance_attach):
            r.compliance_attach = bool(r.compliance)
            r.attachment = not r.compliance_attach
    if r.answer_type == 5 and low.startswith("insert"):
        p.note("answer type 5 -> 1: the question asks for a typed value")
        r.answer_type = 1
        r.attachment = r.compliance_attach = False

    # An upload with no L/M flag is left alone: ERCS has 70 such rows and
    # never uses L, so what the platform does with them is an open question.
    if r.answer_type == 5 and r.attachment and r.compliance_attach:
        keep_m = bool(r.compliance)
        r.attachment, r.compliance_attach = not keep_m, keep_m
        p.note("both upload flags set -> kept " + ("Compliance (M)" if keep_m else "Attachment (L)"))


# --- marks -------------------------------------------------------------------

def set_field(r: Row, fld: str, value):
    if fld == "answer_type":
        r.answer_type = int(value)
    elif fld == "mandatory":
        r.mandatory = value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
    elif fld == "upload_flag":
        r.attachment = value in ("attachment", "both")
        r.compliance_attach = value in ("compliance", "both")


class MarksError(ValueError):
    pass


def _largest_remainder(weights: List[float], total: int) -> List[int]:
    s = sum(weights)
    if s <= 0:
        return [0] * len(weights)
    raw = [w * total / s for w in weights]
    out = [int(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: -(raw[i] - out[i]))
    for i in order[: total - sum(out)]:
        out[i] += 1
    return out


def _rescale_scores(r: Row, new_marks: float, original: Optional[str] = None):
    """Scale option scores so the best option equals the row's new marks.

    `original` is the donor's score string for an inserted row whose scores
    were zeroed on insertion; its shape is what gets scaled.
    """
    try:
        vals = [float(s) for s in r.score_list()]
        if (not vals or max(vals) <= 0) and original:
            vals = [float(s) for s in original.split(",") if s.strip()]
    except ValueError:
        return
    if not vals or max(vals) <= 0:
        return
    k = new_marks / max(vals)
    r.option_scores = ",".join(f"{round(v * k):g}" for v in vals)


def apply_section_marks(plan: Plan, split: Dict[str, float], source: str = "reviewer"):
    """Set a section's total marks, spreading them over its scored rows.

    Rows keep their System/Compliance placement; option scores are rescaled so
    the best option still equals the row's marks. A section with no scored
    rows cannot take marks - that needs a question added, which is a human call.
    """
    for key, target_marks in split.items():
        rows = [p for p in plan.questions if section_key(p.row.section) == key]
        scored = [p for p in rows if (p.row.marks or 0) > 0 or p.weight_hint > 0]
        if not rows:
            raise MarksError(f"section '{key}' is not in this sheet")
        if target_marks and not scored:
            raise MarksError(f"section '{key}' has no scored rows to carry {target_marks:g} marks")
        weights = [(p.row.marks or 0) or p.weight_hint for p in scored]
        if all((p.row.marks or 0) == w for p, w in zip(scored, weights)) and \
                abs(sum(weights) - target_marks) < 1e-9:
            continue
        new = _largest_remainder(weights, int(round(target_marks)))
        for p, m in zip(scored, new):
            r = p.row
            old = r.marks or 0
            r.marks = float(m)
            if r.compliance_attach or (r.compliance is not None and r.system is None):
                r.compliance, r.system = float(m), None
            else:
                r.system, r.compliance = float(m), None
            if r.answer_type in (2, 3):
                _rescale_scores(r, m, p.scores_hint)
            p.note(f"marks {old:g} -> {m} (section split from {source})")
        plan.marks_source[key] = source


def keep_section_totals(plan: Plan, before: Dict[str, float], why: str) -> List[str]:
    """After rows are removed or added, give each section back the marks it had.

    Returns the sections that could not be restored (no scored rows left),
    which need a human marks decision.
    """
    problems = []
    after = {k: v[0] for k, v in plan.section_marks().items()}
    for key, marks in before.items():
        if marks and abs(after.get(key, 0) - marks) > 1e-9:
            try:
                apply_section_marks(plan, {key: marks}, f"section total kept after {why}")
            except MarksError as e:
                problems.append(str(e))
    return problems


def drop_empty_sections(plan: Plan):
    """Remove subtotal rows left with no questions above them."""
    keep, since = [], 0
    for p in plan.rows:
        if p.kind == "subtotal" and since == 0:
            continue
        keep.append(p)
        since = since + 1 if p.kind == "question" else 0
    plan.rows = keep


def spread_marks(plan: Plan, freed: float, why: str) -> Dict[str, float]:
    """Spread marks freed by a removed section over the remaining scored
    sections, in proportion to their marks. Returns the new section split."""
    current = {k: v[0] for k, v in plan.section_marks().items() if v[0] > 0}
    if not current:
        raise MarksError(f"{plan.target.code}: no scored sections left to take {freed:g} marks")
    keys = list(current)
    total = int(round(sum(current.values()) + freed))
    new = dict(zip(keys, _largest_remainder([current[k] for k in keys], total)))
    apply_section_marks(plan, {k: float(v) for k, v in new.items()}, why)
    return new


def rebalance_to_100(plan: Plan, why: str) -> Optional[str]:
    """Bring a sheet back to 100 after removals left marks with nowhere to go.

    The missing (or surplus) marks are spread over the scored sections in
    proportion to the marks the client already gave them, so the client's own
    weighting is kept. Returns a note, or None if the sheet was already at 100.
    """
    t = plan.total()
    if abs(t - TOTAL_MARKS) < 1e-6:
        return None
    new = spread_marks(plan, TOTAL_MARKS - t, why)
    note = (f"marks rebalanced to {TOTAL_MARKS} after {why}: {TOTAL_MARKS - t:+g} spread over the "
            f"other sections in proportion ({', '.join(f'{k} {v:g}' for k, v in new.items())})")
    plan.notes.append(note)
    return note


def finalise(plan: Plan):
    """Apply the template rules (specs/02) once nothing else will change the
    plan's rows: after proposals, review answers and section splits."""
    n = sum(fix_marks_columns(p.row) for p in plan.questions)
    if n:
        plan.notes.append(f"marks repeated in System or Compliance on {n} rows (template rule)")


def check_total(plan: Plan):
    t = plan.total()
    if abs(t - TOTAL_MARKS) > 1e-6:
        marks = plan.section_marks()
        detail = ", ".join(f"{k} {v[0]:g}" for k, v in marks.items() if v[0])
        raise MarksError(f"{plan.target.code}: marks total {t:g}, not {TOTAL_MARKS} ({detail})")


# --- planning ----------------------------------------------------------------

def _ordered_rows(sheet: Sheet):
    items = [("question", r.excel_row, r) for r in sheet.rows]
    items += [("total" if "total marks" in s.label.lower() else "subtotal", s.excel_row, s)
              for s in sheet.structural]
    return sorted(items, key=lambda x: x[1])


def _client_frequency(sheets: List[Sheet], client: str) -> Tuple[Counter, int]:
    own = [s for s in sheets if s.client == client]
    c = Counter()
    for s in own:
        c.update({normalise(r.criteria) for r in s.rows})
    return c, len(own)


class Generator:
    def __init__(self, sheets: List[Sheet], retriever: Retriever,
                 decisions: Optional[Dict[Tuple[str, str], str]] = None,
                 grades: Optional[Dict[str, "Grade"]] = None,
                 rulings: Optional[Dict[Tuple[str, str, str], object]] = None):
        """`grades` (trust.grade) keeps broken and superseded sheets out of
        reuse; `rulings` are standing human decisions keyed (client, question
        key, field), applied before client conventions."""
        self.grades = grades or {}
        self.sheets = usable(sheets, self.grades) if grades else sheets
        self.retriever = retriever
        self.decisions = decisions or {}
        self.rulings = rulings or {}
        self.conventions = Conventions(self.sheets)
        self._freq_cache: Dict[str, Tuple[Counter, int]] = {}

    def _freq(self, client):
        if client not in self._freq_cache:
            self._freq_cache[client] = _client_frequency(self.sheets, client)
        return self._freq_cache[client]

    def _usable(self, m: Match) -> bool:
        g = self.grades.get(m.sheet.name)
        return g is None or g.tier in (CLEAN, CORRECTABLE)

    def _client_rules(self, p: "PlannedRow", client: str, key: str):
        """Standing rulings first, then the client's own convention; a real
        disagreement is recorded on the row for the review to ask about."""
        r = p.row
        for fld, get in FIELDS.items():
            cur = get(r)
            ruled = self.rulings.get((client, key, fld))
            if ruled is not None:
                target, why = ruled, "standing ruling"
            else:
                conv = self.conventions.convention(client, key, fld)
                if conv is None:
                    c = self.conventions.contested(client, key, fld)
                    if c is not None:
                        p.contested.append((fld, dict(c)))
                    continue
                target, why = conv[0], f"{client} convention ({conv[1]} of {conv[2]} uses)"
            if target != cur:
                set_field(r, fld, target)
                p.note(f"{fld} {cur} -> {target}: {why}")

    def plan(self, target: Target, exclude: Optional[set] = None,
             marks: Optional[Dict[str, float]] = None, k: int = 5) -> Plan:
        ranked = self.retriever.rank(target.description, target.client, target.group,
                                     exclude=exclude, k=max(k, 12))
        matches = [m for m in ranked if self._usable(m)]
        if not matches:
            raise ValueError("no usable past sheets to build from")
        skeleton = next((m for m in matches if m.sheet.client == target.client), matches[0])
        skipped = []
        for m in ranked:
            if m is skeleton:
                break
            if not self._usable(m) and m.sheet.client == target.client:
                g = self.grades[m.sheet.name]
                skipped.append(f"skipped {m.sheet.name} ({g.tier}: {', '.join(g.reasons)})")
        sk = skeleton.sheet
        same_code = bool(sk.code) and sk.code == target.code
        same_desc = normalise(sk.description or "") == normalise(target.description)
        same_category = same_code or same_desc
        freq, n_own = self._freq(sk.client)
        name = target.name_in_text

        rows: List[PlannedRow] = []
        last_section = None
        for kind, xr, obj in _ordered_rows(sk):
            if kind != "question":
                rows.append(PlannedRow(kind=kind, source=f"{sk.name}!r{xr}"))
                continue
            p = PlannedRow(kind="question", row=deepcopy(obj), source=f"{sk.name}!r{xr}")
            r = p.row
            if r.section != last_section:
                p.section_label = r.section
                last_section = r.section
            p.ctx_key = context_key(sk.rows, sk.rows.index(obj))
            p.client = sk.client

            text = fill_placeholder(swap_client(r.criteria, name), name)
            if text != r.criteria:
                p.note(f"text: client name set to {name}")
                r.criteria = text
            if sk.client:
                self._client_rules(p, sk.client, p.ctx_key)
            _apply_corrections(p, self.decisions)

            share = freq[normalise(obj.criteria)] / n_own if n_own else 1.0
            if not same_category and share < CATEGORY_SPECIFIC_SHARE:
                p.needs_reading = True
                p.note(f"specific to '{sk.description}' (in {share:.0%} of {sk.client} sheets); "
                       f"confirm it fits this category", adapted=False)
            rows.append(p)

        plan = Plan(target=target, skeleton=skeleton, matches=matches[:k], rows=rows,
                    n_columns=len(LAYOUT))
        plan.notes += skipped
        plan.same_category = same_category
        # Weak: neither the topic (text) nor the client's own grouping links
        # the source to this category - it was chosen by client and kind only.
        plan.weak_match = (not same_category and skeleton.text_score < DONOR_MIN_TEXT
                           and not any(r.startswith("same group") for r in skeleton.reasons))
        if same_code and not same_desc:
            plan.notes.append(f"description differs from the existing {sk.code} sheet "
                              f"(was '{sk.description}'); the notice may have been revised")
        plan.marks_source = {key: sk.name for key in plan.section_marks()}
        plan.marks_alternatives = self._marks_alternatives(plan, matches)
        if not same_category:
            plan.suggestions = self._suggestions(plan, matches, name)
        if marks:
            apply_section_marks(plan, marks)
        plan.findings = validate_sheet(to_sheet(plan))
        return plan

    def _marks_alternatives(self, plan: Plan, matches: List[Match]) -> Dict[str, Dict[str, float]]:
        """Section marks in the other close matches from the same client."""
        out: Dict[str, Dict[str, float]] = defaultdict(dict)
        for m in matches:
            if m.sheet.client != plan.target.client and m is not plan.skeleton:
                continue
            for r in m.sheet.rows:
                key = section_key(r.section)
                out[key][m.sheet.name] = out[key].get(m.sheet.name, 0.0) + (r.marks or 0)
        return dict(out)

    def _suggestions(self, plan: Plan, matches: List[Match], name: str) -> List[Suggestion]:
        """Rows the closest other categories asked that this plan does not.

        Donors are the next best matches from any client. A donor row is
        offered if it is specific to its own category (rare in its client's
        sheets) or sits in a section expected to vary by category.
        """
        have = {normalise(p.row.criteria) for p in plan.questions}
        donors = [m for m in matches if m is not plan.skeleton
                  and (m.sheet.client == plan.target.client or m.text_score >= DONOR_MIN_TEXT)]
        out = []
        for m in donors[:MAX_DONORS]:
            freq, n = self._freq(m.sheet.client)
            for r in m.sheet.rows:
                key = section_key(r.section)
                nk = normalise(r.criteria)
                if nk in have or key in GENERIC_SECTIONS:
                    continue
                share = freq[nk] / n if n else 1.0
                if share >= CATEGORY_SPECIFIC_SHARE and key not in VARIABLE_SECTIONS:
                    continue
                r2 = deepcopy(r)
                r2.criteria = fill_placeholder(swap_client(r2.criteria, name), name)
                have.add(nk)
                out.append(Suggestion(key, r2, f"{m.sheet.name}!r{r.excel_row}",
                                      f"used for '{m.sheet.description}' (match {m.score:.2f})",
                                      m.text_score))
        return out[:MAX_SUGGESTIONS]


def to_sheet(plan: Plan) -> Sheet:
    """The plan as a parsed Sheet, so the same validator runs on output as on input."""
    s = Sheet(source_file="<generated>", name=plan.target.code, title=f"{plan.target.code}: {plan.target.description}",
              code=plan.target.code, description=plan.target.description, client=plan.target.client,
              header_row=4, n_columns=plan.n_columns)
    running = [0.0, 0.0, 0.0]
    xr = 5
    for p in plan.rows:
        if p.kind == "question":
            r = deepcopy(p.row)
            r.excel_row, r.sheet, r.source_file = xr, s.name, s.source_file
            s.rows.append(r)
            running = [running[0] + (r.marks or 0), running[1] + (r.system or 0),
                       running[2] + (r.compliance or 0)]
        elif p.kind == "subtotal":
            s.structural.append(StructuralRow(s.name, xr, "Sub Total", *running))
            running = [0.0, 0.0, 0.0]
        else:
            tot = [sum(r.marks or 0 for r in s.rows), sum(r.system or 0 for r in s.rows),
                   sum(r.compliance or 0 for r in s.rows)]
            s.structural.append(StructuralRow(s.name, xr, "Total Marks", *tot))
        xr += 1
    return s


def _canonical_index(key: str) -> int:
    keys = [k for _, k in SECTION_KEYS]
    return keys.index(key) if key in keys else len(keys)


def insert_rows(plan: Plan, section: str, new_rows: List[Row], origin: str, source: str,
                reason: str, section_label: Optional[str] = None):
    """Insert rows at the end of a section (before its subtotal, if any).

    If the sheet has no such section, one is created in canonical position
    with its own subtotal row. Inserted rows arrive with zero marks and the
    donor's marks kept as a weight hint; a marks split puts real marks on them.
    """
    idx = None
    for i, p in enumerate(plan.rows):
        if p.kind == "question" and section_key(p.row.section) == section:
            idx = i
    created = idx is None
    if created:
        pos = _canonical_index(section)
        start = next((i for i, p in enumerate(plan.rows)
                      if p.kind == "question" and _canonical_index(section_key(p.row.section)) > pos),
                     None)
        if start is None:
            start = next(i for i, p in enumerate(plan.rows) if p.kind == "total")
        label = section_label or new_rows[0].section or section
        plan.rows.insert(start, PlannedRow(kind="subtotal", source="new section"))
        idx = start - 1
    else:
        label = plan.rows[idx].row.section
    for j, r in enumerate(new_rows):
        r.section = label
        hint = r.marks or 0.0
        scores_hint = r.option_scores
        r.marks = 0.0 if r.marks is not None else None
        if r.system:
            r.system = 0.0
        if r.compliance:
            r.compliance = 0.0
        if r.answer_type in (2, 3) and r.score_list():
            r.option_scores = ",".join("0" for _ in r.score_list())
        p = PlannedRow(kind="question", row=r, origin=origin, source=source, needs_reading=True,
                       notes=[reason] + ([f"marks 0 for now (donor gave {hint:g}); set the section split"]
                                         if hint else []),
                       weight_hint=hint, scores_hint=scores_hint,
                       section_label=label if (created and j == 0) else None)
        plan.rows.insert(idx + 1 + j, p)
    plan.findings = validate_sheet(to_sheet(plan))


def drop_row(plan: Plan, index: int, reason: str):
    p = plan.rows[index]
    if p.kind != "question":
        raise ValueError("only question rows can be dropped")
    if p.section_label:
        nxt = next((q for q in plan.rows[index + 1:] if q.kind == "question"), None)
        if nxt is not None and nxt.row.section == p.row.section:
            nxt.section_label = p.section_label
    del plan.rows[index]
    plan.dropped.append((p, reason))
