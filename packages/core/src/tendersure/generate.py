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

import hashlib
import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import Row, Sheet, StructuralRow, normalise, section_key
from .schema import CLIENT_NAMES, LAYOUT, SECTION_KEYS, TOTAL_MARKS, INDIVIDUAL
from .similarity import Match, Retriever
from .trust import CLEAN, CORRECTABLE, FIELDS, Conventions, Grade, context_key, usable
from .validate import (SUB_CATEGORY, SUB_CATEGORY_QUESTION, TRADE_LICENSE, Finding, asks_for_document,
                       fix_marks_columns,
                       fix_option_scores, fix_stray_options, fix_upload_flags, sub_category_options, validate_sheet)

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
    eligibility: List[str] = field(default_factory=list)    # documents the Eligibility section asks for
    gate_question: bool = False     # the business-type question gates the sheet
    applies_to: List[str] = field(default_factory=list)     # business types the category accepts
    licence: List[str] = field(default_factory=list)        # the draft's requirement columns (spec 07)
    hr: List[str] = field(default_factory=list)
    technical: List[str] = field(default_factory=list)
    brief: str = ""                 # the client's brief for the run (spec 07 B), read first by the model
    client_rules: list = field(default_factory=list)   # spec 12 C: applied first among the template rules
    country: str = ""               # ISO code (ET, KE): past sheets from this country come first (spec 12 S)

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
    freed: Optional[Tuple[str, float]] = None  # (section, its marks before) when the row's marks moved off it

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
    benchmark: Optional[dict] = None  # the client's tab it is compared with (benchmark.reference)
    fixed_sections: set = field(default_factory=set)  # marks the client set itself: no rebalance moves them (spec 14)

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
    """Every other client's names - codes and full names, longest first -
    become the target's ("Norwegian Refugee Council" as well as "NRC")."""
    from .privacy import CLIENT_ALIASES
    own = target_name.lower()
    names = {n for c, aliases in CLIENT_ALIASES.items()
             if own not in {c.lower()} | {a.lower() for a in aliases} for n in aliases}
    names |= {n for n in CLIENT_NAMES if n.lower() != own}
    for name in sorted(names, key=len, reverse=True):
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
    if r.answer_type is None and not r.option_list():
        guess = 4 if re.search(r"\byes\s*/\s*no\b", low) else 5 if re.search(r"\b(attach|upload)\b", low) else None
        if guess:
            p.note(f"answer type {guess} from the wording: the sheet gave none")
            p.needs_reading = True
            r.answer_type = guess
            if guess == 5 and not (r.attachment or r.compliance_attach):
                r.compliance_attach = bool(r.compliance) or r.compliance == 0
                r.attachment = not r.compliance_attach
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


def apply_section_marks(plan: Plan, split: Dict[str, float], source: str = "reviewer", share_unscored: bool = False):
    """Set a section's total marks, spreading them over its scored rows.

    Rows keep their System/Compliance placement; option scores are rescaled so
    the best option still equals the row's marks. A section with no scored
    rows cannot take marks - that needs a question added, which is a human
    call - unless the team sets the split itself (`share_unscored`): then the
    section's rows that can be scored (uploads, Yes/No, selections) share the
    marks equally.
    """
    for key, target_marks in split.items():
        rows = [p for p in plan.questions if section_key(p.row.section) == key]
        scored = [p for p in rows if (p.row.marks or 0) > 0 or p.weight_hint > 0]
        if not rows:
            raise MarksError(f"section '{key}' is not in this sheet")
        if target_marks and not scored and share_unscored:
            scored = [p for p in rows if p.row.answer_type in (2, 3, 4, 5)]
            for p in scored:
                p.weight_hint = 1.0                       # equal shares
        if target_marks and not scored:
            raise MarksError(f"section '{key}' has no scored rows to carry {target_marks:g} marks")
        if share_marks(scored, target_marks, f"section split from {source}"):
            plan.marks_source[key] = source


def share_marks(scored: List[PlannedRow], target_marks: float, why: str) -> bool:
    """Spread `target_marks` over rows in proportion to their marks (else their weight hints),
    in whole marks. False when they already carry exactly that."""
    weights = [(p.row.marks or 0) or p.weight_hint for p in scored]
    if all((p.row.marks or 0) == w for p, w in zip(scored, weights)) and abs(sum(weights) - target_marks) < 1e-9:
        return False
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
        p.note(f"marks {old:g} -> {m} ({why})")
    return True


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


def rebalance_others(plan: Plan, fixed: set, why: str) -> Optional[str]:
    """Bring a sheet back to 100 when the client set some sections' marks itself (spec 14): the
    difference is spread over the other scored sections in proportion, the fixed ones untouched."""
    t = plan.total()
    if abs(t - TOTAL_MARKS) < 1e-6:
        return None
    fixed = set(fixed) | plan.fixed_sections
    current = {k: v[0] for k, v in plan.section_marks().items() if v[0] > 0 and k not in fixed}
    if not current:
        note = f"marks: the sheet totals {t:g} after {why}, and no other section can take the difference"
        plan.notes.append(note)
        return note
    keys = list(current)
    new = dict(zip(keys, _largest_remainder([current[k] for k in keys],
                                            int(round(sum(current.values()) + TOTAL_MARKS - t)))))
    apply_section_marks(plan, {k: float(v) for k, v in new.items()}, why)
    note = (f"marks rebalanced to {TOTAL_MARKS} after {why}: {TOTAL_MARKS - t:+g} spread over the other "
            f"sections in proportion ({', '.join(f'{k} {v:g}' for k, v in new.items())})")
    plan.notes.append(note)
    return note


def rebalance_to_100(plan: Plan, why: str, target: float = TOTAL_MARKS) -> Optional[str]:
    """Bring a sheet back to 100 after removals left marks with nowhere to go.

    The missing (or surplus) marks are spread over the scored sections in
    proportion to the marks the client already gave them, so the client's own
    weighting is kept. Returns a note, or None if the sheet was already at 100.
    `target` is lower when a client rule's added question brings the rest later (spec 17 A).
    """
    t = plan.total()
    if abs(t - target) < 1e-6:
        return None
    new = spread_marks(plan, target - t, why)
    note = (f"marks rebalanced to {target:g} after {why}: {target - t:+g} spread over the "
            f"other sections in proportion ({', '.join(f'{k} {v:g}' for k, v in new.items())})")
    plan.notes.append(note)
    return note


ELIGIBILITY_LABEL = "Eligibility"


def _standard_trade_license() -> Row:
    return Row(source_file="", sheet="", excel_row=0, section=ELIGIBILITY_LABEL, sub_section="N/A",
               criteria="Please attach a valid Trade license", marks=0.0,
               archive_short_name="Trade License", acronym="TL", answer_type=5,
               mandatory=True, compliance_attach=True, eligibility=True)


def _relabel_sections(plan: Plan):
    """Column B carries a section's label on its first row only."""
    prev = None
    for p in plan.rows:
        if p.kind != "question":
            prev = None
            continue
        p.section_label = p.row.section if p.row.section != prev else None
        prev = p.row.section


def _trade_license_to_move(plan: Plan, in_section: List[PlannedRow]) -> List[PlannedRow]:
    """Trade License questions outside Eligibility; a standard one if the
    sheet has none. One that carries marks moves too, as the 0-mark
    mandatory upload, and its marks stay in the section it left (spread over
    that section's other scored questions; else over the sheet) - agreed
    2026-10-30, so a past sheet scoring the licence in ESG is still written."""
    found = [p for p in plan.questions if TRADE_LICENSE.search(p.row.criteria) and p not in in_section]
    for p in found:
        if p.row.marks:
            key, marks = section_key(p.row.section), p.row.marks
            before = plan.section_marks()[key][0]
            std = _standard_trade_license()
            p.note(f"Trade License moved to Eligibility as the 0-mark mandatory upload; its {marks:g} marks "
                   f"stay in '{p.row.section}'; was: {p.row.criteria}")
            p.needs_reading = True
            r = p.row
            r.criteria, r.marks, r.system, r.compliance = std.criteria, 0.0, None, 0.0
            r.option_scores = None
            p.freed = (key, before)
    if not found and not in_section:
        return [PlannedRow(kind="question", row=_standard_trade_license(), origin=ADAPTED,
                           source="standard Trade License row",
                           notes=["Trade License added: the source sheet had none"])]
    return found


def _keep_freed_marks(plan: Plan, moved: List[PlannedRow]):
    """The marks a moved Trade License carried stay in its old section; with
    no other scored question there, they are spread over the sheet."""
    for p in moved:
        if not p.freed:
            continue
        key, before = p.freed
        problems = keep_section_totals(plan, {key: before}, "the Trade License moved to Eligibility") \
            if any(section_key(q.row.section) == key for q in plan.questions) else [f"section '{key}' is empty"]
        if problems:
            rebalance_to_100(plan, "the Trade License moved to Eligibility")
        p.freed = None


def _eligibility_documents(plan: Plan, in_section: List[PlannedRow]) -> List[PlannedRow]:
    """An upload row per document in the category's Eligibility column (from
    the categories table) that the sheet does not ask for yet (spec 02 G3)."""
    asked = " ".join(p.row.criteria.lower() for p in plan.questions)
    out = []
    for doc in plan.target.eligibility:
        if doc.lower() in asked:
            continue
        out.append(PlannedRow(kind="question", origin=ADAPTED, source="categories table",
                              notes=[f"eligibility document from the categories table: {doc}"],
                              row=Row(source_file="", sheet="", excel_row=0, criteria=f"Please attach a valid {doc}",
                                      marks=0.0, archive_short_name=doc if len(doc.split()) <= 4 else None,
                                      answer_type=5, mandatory=True, compliance_attach=True, eligibility=True)))
    return out


def _eligibility_position(plan: Plan, in_section: List[PlannedRow]) -> int:
    """After the section's own rows; else after the sections that precede it
    (and a Sub Total closing them)."""
    if in_section:
        return plan.rows.index(in_section[-1]) + 1
    before = _canonical_index("eligibility")
    at = 0
    for i, p in enumerate(plan.rows):
        if p.kind == "question" and _canonical_index(section_key(p.row.section)) < before:
            at = i + 1
    if at < len(plan.rows) and plan.rows[at].kind == "subtotal":
        at += 1
    return at


def _flag_eligibility(plan: Plan):
    """The documents asked in the Eligibility section are mandatory,
    compliance-flagged uploads marked Eligibility; a typed row there ("Insert
    company registration certificate number", as NRC's template has) stays as
    it is, unmarked - as in the feedback's own example. No row outside the
    section is Eligibility."""
    for p in plan.questions:
        r = p.row
        if section_key(r.section) != "eligibility" or not asks_for_document(r):
            r.eligibility = False
            continue
        if r.answer_type != 5:
            p.note(f"answer type {r.answer_type} -> 5: Eligibility asks for a document")
        r.eligibility, r.answer_type, r.mandatory, r.compliance_attach = True, 5, True, True


REGISTRATION_LABEL = "Registration and Statutory Requirements"


def _stays_in_eligibility(plan: Plan, p: PlannedRow) -> bool:
    """Feedback 7: the Eligibility section holds the Trade License, the documents the draft
    states (categories table) and the category's licence rows - nothing else."""
    from .requirements import GRADE_ROW
    text = p.row.criteria.lower()
    return bool(TRADE_LICENSE.search(p.row.criteria) or GRADE_ROW.search(p.row.criteria)
                or any(d.lower() in text for d in plan.target.eligibility + plan.target.licence)
                or any("licence column" in n for n in p.notes))


def _registration_out_of_eligibility(plan: Plan, in_section: List[PlannedRow]) -> List[PlannedRow]:
    """A past sheet's Eligibility block often holds the registration documents (NRC's: company
    registration, PIN, CR12, tax compliance). They move to Registration and Statutory
    Requirements - the sheet's own, else one created right after Eligibility. Returns the rows
    that stay."""
    movers = [p for p in in_section if not _stays_in_eligibility(plan, p)]
    if not movers:
        return in_section
    stays = [p for p in in_section if p not in movers]
    reg = [p for p in plan.questions if section_key(p.row.section) == "registration"]
    anchor = reg[-1] if reg else (stays[-1] if stays else None)
    at = plan.rows.index(movers[0])
    for p in movers:
        plan.rows.remove(p)
        p.row.section = reg[0].row.section if reg else REGISTRATION_LABEL
        p.row.sub_section = reg[-1].row.sub_section if reg else "N/A"
        p.note(f"moved from Eligibility to '{p.row.section}': Eligibility holds only the Trade License "
               f"and the documents the client states")
    if anchor is not None:
        at = plan.rows.index(anchor) + 1
    plan.rows[at:at] = movers
    return stays


_ID_ROW = re.compile(r"\b(national\s+id|id\b|passport|fayda)", re.I)


def _national_id_text(country: str) -> Tuple[str, str]:
    if country == "ET":
        return "Please attach a copy of your Fayda ID (national ID)", "Fayda ID"
    return "Please attach a copy of your national ID or passport", "National ID"


def _national_id(plan: Plan, in_section: List[PlannedRow]) -> List[PlannedRow]:
    """Spec 12 R4: a gated category's Eligibility asks individuals for their national ID (the
    Fayda ID in Ethiopia). An ID upload the sheet already asks individuals moves in, worded so."""
    t = plan.target
    if not t.gate_question or INDIVIDUAL not in t.applies_to:
        return []
    text, short = _national_id_text(t.country)
    there = next((p for p in plan.questions if _ID_ROW.search(p.row.criteria) and asks_for_document(p.row)
                  and str(p.row.applies_to or "").strip() == INDIVIDUAL), None)
    if there is not None:
        if there.row.criteria != text:
            there.note(f"national ID for individuals (spec 12 R4); was: {there.row.criteria}")
            there.row.criteria, there.row.archive_short_name, there.row.acronym = text, short, None
        there.row.marks, there.row.system, there.row.compliance = 0.0, None, 0.0
        return [] if there in in_section else [there]
    return [PlannedRow(kind="question", origin=ADAPTED, source="national ID for individuals",
                       notes=["national ID for individuals on a gated category (spec 12 R4)"],
                       row=Row(source_file="", sheet="", excel_row=0, criteria=text, marks=0.0,
                               archive_short_name=short, answer_type=5, mandatory=True, compliance_attach=True,
                               eligibility=True, applies_to=INDIVIDUAL))]


def _eligibility_audiences(plan: Plan):
    """On a gated sheet the Trade License is for firms and the national ID for individuals."""
    t = plan.target
    if not t.gate_question:
        return
    firms = [b for b in t.applies_to if b != INDIVIDUAL]
    for p in plan.questions:
        if section_key(p.row.section) != "eligibility":
            continue
        if TRADE_LICENSE.search(p.row.criteria) and firms:
            p.row.applies_to = ", ".join(firms)
        elif _ID_ROW.search(p.row.criteria):
            p.row.applies_to = INDIVIDUAL


def place_eligibility(plan: Plan):
    """An Eligibility section right after Company Information, holding the
    Trade License and the category's own eligibility documents (spec 02 G2,
    G3)."""
    from .requirements import licence_rows
    in_section = [p for p in plan.questions if section_key(p.row.section) == "eligibility"]
    in_section = _registration_out_of_eligibility(plan, in_section)
    moving = _trade_license_to_move(plan, in_section)
    moving += _eligibility_documents(plan, in_section)
    moving += [p for p in licence_rows(plan, in_section) if p not in moving]
    moving += [p for p in _national_id(plan, in_section) if p not in moving]
    for p in moving:
        if p in plan.rows:
            plan.rows.remove(p)
            p.note(f"moved from '{p.row.section}' to Eligibility")
        p.row.section = in_section[0].row.section if in_section else ELIGIBILITY_LABEL
        p.row.sub_section = in_section[-1].row.sub_section if in_section else "N/A"
    for p in moving:                           # the client's own documents: nothing for the team to decide
        if p.source == "categories table" or any(n.startswith("from the category's licence") for n in p.notes):
            p.needs_reading = False
    at = _eligibility_position(plan, in_section)
    plan.rows[at:at] = moving
    _keep_freed_marks(plan, moving)
    _flag_eligibility(plan)
    _eligibility_audiences(plan)
    _relabel_sections(plan)


BUSINESS_TYPE = re.compile(r"organi[sz]ation\s+type", re.I)


def _sub_category_anchor(plan: Plan) -> Optional[int]:
    """The row the sub-category question follows: the business-type question,
    else the last Company Information row."""
    rows = [(i, p) for i, p in enumerate(plan.rows) if p.kind == "question"]
    hit = next((i for i, p in rows if BUSINESS_TYPE.search(p.row.criteria)), None)
    if hit is None:
        hit = max((i for i, p in rows if section_key(p.row.section) == "company"), default=None)
    return hit


def set_organization_types(plan: Plan):
    """Spec 12 R3: Organization type offers the category's business types on every sheet - the
    gate question sets them on a gated one (gating.apply), this does it on the others."""
    types = plan.target.applies_to
    if not types or plan.target.gate_question:
        return
    options = ", ".join(types)
    for p in plan.questions:
        r = p.row
        if BUSINESS_TYPE.match(r.criteria.strip()) and r.options != options:
            p.note(f"organization type: options set to the category's business types ({options})")
            r.answer_type, r.options, r.option_scores = 2, options, ",".join("0" for _ in types)


def place_sub_category_question(plan: Plan):
    """One multi-select question listing the advert's sub-categories, right
    after the business-type question (spec 02 G5; precedent ERCS-ET-2026-039).
    A copied one takes this category's options, or goes if it has none."""
    subs = plan.target.sub_categories
    found = [i for i, p in enumerate(plan.rows)
             if p.kind == "question" and SUB_CATEGORY.search(p.row.criteria)]
    for i in reversed(found[1:] if subs else found):
        drop_row(plan, i, "repeated sub-category question" if subs else
                 "sub-category question copied from another category; the advert gives this one none")
    if not subs:
        return
    if found:                       # one copied, or drafted by the model: it moves into place
        p = plan.rows.pop(found[0])
    else:
        p = PlannedRow(kind="question", source="advert sub-categories",
                       origin=ADAPTED, notes=["sub-category question added from the advert"],
                       row=Row(source_file="", sheet="", excel_row=0, criteria=SUB_CATEGORY_QUESTION, marks=0.0))
    at = _sub_category_anchor(plan)
    plan.rows.insert(at + 1 if at is not None else 0, p)
    r = p.row
    if at is not None:
        anchor = plan.rows[at].row
        if found and r.section != anchor.section:
            p.note(f"moved from '{r.section}' to sit after '{anchor.criteria}'")
        r.section, r.sub_section = anchor.section, anchor.sub_section
    if r.criteria != SUB_CATEGORY_QUESTION:
        p.note(f"wording set to the standard question (was: {r.criteria})")
        r.criteria = SUB_CATEGORY_QUESTION
    options = sub_category_options(subs)
    if found and r.options != options:
        p.note(f"options set to this category's sub-categories (were: {r.options})")
    if any("," in s for s in subs):
        p.note("a comma inside a sub-category name became ' / ', so it stays one option", adapted=False)
    r.answer_type, r.mandatory, r.options = 3, True, options
    r.option_scores = ",".join("0" for _ in subs)
    if r.marks:
        p.note(f"sub-category question carries {r.marks:g} marks; options all score 0", adapted=False)
        p.needs_reading = True


def finalise(plan: Plan):
    """Apply the template rules (specs/02) once nothing else will change the
    plan's rows: after proposals, review answers and section splits. Running
    them here also keeps the AI's cached answers valid, as its prompts are
    built from the plan before this point. The category's own licence, HR
    and technical items come first (spec 07 R4)."""
    from . import client_rules
    from .requirements import place_requirements
    client_rules.apply(plan, plan.target.client_rules)
    place_requirements(plan)
    set_organization_types(plan)
    complete_blocks(plan)
    place_sub_category_question(plan)
    place_eligibility(plan)
    _relabel_sections(plan)
    client_rules.apply_marks(plan, plan.target.client_rules)       # last: nothing after moves these marks
    for p in plan.questions:
        note = fix_missing_options(p.row) or fix_option_scores(p.row)
        if note:
            p.note(note)
            p.needs_reading = p.needs_reading or note.startswith("answer type")
    n = sum(fix_marks_columns(p.row) for p in plan.questions)
    if n:
        plan.notes.append(f"marks repeated in System or Compliance on {n} rows (template rule)")
    for p in plan.questions:                 # last: they read where the marks now sit
        for fix in (fix_upload_flags, fix_stray_options):
            note = fix(p.row)
            if note:
                p.note(note)


def ai_cache_key(plan: Plan, prompt: str, step: str = "") -> str:
    """A model answer's key in the build cache: the prompt with the sheet code
    taken out (it names the category and holds everything the answer depends
    on), so renaming the prefix, or fixing one, reuses every answer paid for."""
    text = prompt.replace(plan.target.code, "<code>")
    return f"{step}|{hashlib.sha1(text.encode()).hexdigest()[:16]}"


_YES_NO_WORDING = re.compile(r"^(do|does|did|is|are|was|were|has|have|had|can|will|would)\b|\byes\s*/\s*no\b", re.I)


def fix_missing_options(r: Row) -> Optional[str]:
    """A single or multi select with no options cannot be answered (AT-NO-OPTIONS):
    a question worded for Yes/No becomes Yes/No, any other is answered in words.
    The row is flagged for the team either way."""
    if r.answer_type not in (2, 3) or r.option_list():
        return None
    old = r.answer_type
    r.answer_type, r.options, r.option_scores = (4 if _YES_NO_WORDING.search(r.criteria.strip()) else 1), None, None
    return f"answer type {old} -> {r.answer_type}: a choice with no options listed; add options to keep it a choice"


def complete_blocks(plan: Plan):
    """Spec 03 C17: every Trade Reference and shareholder block asks what its
    first block asks. A typed field a later block lacks ("Period of the
    contract" in Ref 2) is copied in from the first, with 0 marks, before the
    block's evidence upload."""
    for section in ("trade_reference", "shareholder"):
        blocks: Dict[str, List[PlannedRow]] = {}
        for p in plan.questions:
            if section_key(p.row.section) == section and p.row.sub_section:
                blocks.setdefault(p.row.sub_section, []).append(p)
        names = list(blocks)
        if len(names) < 2:
            continue
        first = [p for p in blocks[names[0]] if not asks_for_document(p.row)]
        for name in names[1:]:
            have = {re.sub(r"\s*\d+", "#", normalise(p.row.criteria)) for p in blocks[name]}
            missing = [p for p in first if re.sub(r"\s*\d+", "#", normalise(p.row.criteria)) not in have]
            if not missing:
                continue
            block = blocks[name]
            upload = next((p for p in block if asks_for_document(p.row)), None)
            at = plan.rows.index(upload) if upload else plan.rows.index(block[-1]) + 1
            for m in missing:
                row = deepcopy(m.row)
                row.sub_section, row.marks, row.system, row.compliance = name, 0.0, None, None
                plan.rows.insert(at, PlannedRow(kind="question", row=row, origin=ADAPTED, source=m.source,
                                                notes=[f"copied from '{names[0]}': '{name}' did not ask it"]))
                at += 1


def one_label_per_section(plans: List[Plan]) -> int:
    """Every sheet of a batch spells a section the same way: the label most of the batch's rows
    already use ("Technical Specifications-TECH", not also "...- TECH", "Technical Specification-TECH"
    or "Technical"). Returns how many rows changed."""
    keys = {k for _, k in SECTION_KEYS}
    seen: Dict[str, Counter] = defaultdict(Counter)
    for p in plans:
        for q in p.questions:
            if section_key(q.row.section) in keys:
                seen[section_key(q.row.section)][q.row.section] += 1
    best = {k: c.most_common(1)[0][0] for k, c in seen.items()}
    changed = 0
    for p in plans:
        for q in p.questions:
            label = best.get(section_key(q.row.section))
            if label and q.row.section != label:
                q.row.section = label
                changed += 1
        _relabel_sections(p)
    return changed


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
                                     exclude=exclude, k=max(k, 40 if target.country else 12))
        matches = [m for m in ranked if self._usable(m)]
        if not matches:
            raise ValueError("no usable past sheets to build from")
        # The client's own sheets first, then sheets from its country (spec 12 S): an Ethiopian job
        # starts from Ethiopian questions, never Kenyan ones when an Ethiopian sheet is there.
        if target.country:
            from .research import sheet_country
            home = {m.sheet.name for m in matches if sheet_country(m.sheet) == target.country}
            matches.sort(key=lambda m: (m.sheet.client != target.client, m.sheet.name not in home))
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
        # The client's own sheet for this category; another client's sheet with the same description
        # is a close match to adapt, not the answer (its questions are that client's choices).
        same_category = same_code or (same_desc and sk.client == target.client)
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
        if target.country and sk.client != target.client:
            from .research import sheet_country
            there = sheet_country(sk) or "unknown"
            if there != target.country:
                plan.notes.append(f"no past sheet from {target.country} fits this category: built from {sk.name} "
                                  f"({there}); check its questions suit {target.country}")
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
    # Inserted rows join the Sub Section of the row above, so its merged block
    # is not split (a drafted row has none; a donor's may be named differently).
    above = None if created else plan.rows[idx].row
    for j, r in enumerate(new_rows):
        r.section = label
        if above is not None:
            r.sub_section = above.sub_section
        else:
            r.sub_section = new_rows[0].sub_section or "N/A"
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


# --- research (spec 05): only where the bank has nothing ---------------------------

_ABBREVIATION = re.compile(r"\b[A-Z][A-Z0-9]{1,5}\b")
# Abbreviations every team knows; research is for the ones nobody on it does.
KNOWN_ABBREVIATIONS = {"ISO", "TIN", "VAT", "PIN", "ID", "CV", "PO", "LPO", "NGO", "NGOS", "ICT", "IT", "HR", "UN",
                       "EU", "PDF", "SLA", "WASH", "CCTV", "UPS", "LTD", "PLC", "NB", "OEM", "HSE", "SHE", "PPE",
                       "TV", "TVS", "AC", "MEP", "GPS", "USB", "IATA", "ESG", "QA", "KG", "SMS", "SDLC"}
RESEARCHED_QUESTIONS = 5     # at most, per sheet


def unknown_abbreviations(plans: List[Plan], known: set) -> List[str]:
    """Abbreviations in document questions that neither the team's usual list
    nor the glossary explains (spec 05 K4)."""
    found = Counter()
    for plan in plans:
        for p in plan.questions:
            if p.row.answer_type == 5:
                found.update(a for a in _ABBREVIATION.findall(p.row.criteria)
                             if a not in KNOWN_ABBREVIATIONS and a not in known and not a.isdigit())
    return sorted(found)


def apply_research(plan: Plan, table, country: str):
    """Research findings on one sheet. For a category no past sheet covers
    (weak match), researched technical questions are added as NEW, 0 marks,
    with their sources; licences are listed in the notes. Document
    abbreviations get their official names noted. Nothing is made mandatory."""
    from .research import describe, document_names, sources_text
    t = plan.target.description
    if plan.weak_match:
        q = table.lookup("questions", t, country)
        if q:
            asked = {normalise(p.row.criteria) for p in plan.questions}
            rows = []
            for x in q.proposal.get("questions", []):
                if normalise(x["question"]) in asked:
                    continue
                at = int(x.get("answer_type") or 1)
                opts = ", ".join(o.strip() for o in str(x.get("options") or "").split(",") if o.strip())
                if at in (2, 3) and not opts:
                    at = 1                          # a choice with nothing to choose from: answered in words
                rows.append(Row(source_file="", sheet="", excel_row=0, criteria=x["question"].strip(), marks=0.0,
                                answer_type=at, options=opts if at in (2, 3) else None,
                                option_scores=",".join("0" for _ in opts.split(",")) if at in (2, 3) else None,
                                attachment=at == 5))
            if rows:
                insert_rows(plan, "technical", rows[:RESEARCHED_QUESTIONS], NEW, "research",
                            "researched: no past sheet covers this category. Sources: " + sources_text(q))
        lic = table.lookup("licences", t, country)
        if lic and lic.proposal.get("licences"):
            plan.notes.append(f"licences usually held for this category ({country or 'no country'}), not applied: "
                              f"{describe(lic)}. Sources: {sources_text(lic)}")
    names = document_names(table, country)
    for p in plan.questions:
        for a in _ABBREVIATION.findall(p.row.criteria):
            if a in names and not any(a in n for n in p.notes):
                official, source = names[a]
                p.note(f"{a}: {official} (source: {source})", adapted=False)
