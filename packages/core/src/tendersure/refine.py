"""Category refinement (spec 01): a draft category list -> a revised list for
the client's review -> the final list and the categories table.

Step A (propose) reads a draft list as the clients write it and proposes, per
draft row, what the team does by hand today (NRC's "Revised and Add."
tab): wording fixed (Renamed), duplicates folded (Repeated), overlapping rows
merged (Merged) or raised as a question, bundled trades split (Separated),
categories common in past exercises suggested (Additional (suggested)),
questions for the client, and the columns generation needs (sub-categories,
Eligibility, Gate Question, Applies To).

Step B (apply) takes the team's and the client's decisions and writes the
final list and the categories table (spec 02's input).

The model proposes; code keeps the last word. Every draft row is accounted
for (a row the model skips is kept as it is); nothing is removed except a
true duplicate; a merge or separation without a reason becomes a question;
Gate Question always agrees with Applies To. The number of categories is a
business decision: the tool reports it against a target, never forces it.
"""

import csv
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from . import brief as cb
from .categories import Category, category_kind
from .llm import LLMError, make_llm
from .schema import BUSINESS_TYPES, INDIVIDUAL
from .similarity import TfidfEmbedder, cosine

log = logging.getLogger("tendersure")

KEPT, RENAMED, MERGED, SEPARATED, REPEATED = "Kept", "Renamed", "Merged", "Separated", "Repeated"
ADDITIONAL, REMOVED = "Additional (suggested)", "Removed"
STATUSES = [KEPT, RENAMED, MERGED, SEPARATED, REPEATED, ADDITIONAL, REMOVED]
DECISIONS = ("", "accept", "reject", "remove", "edit", "split", "merge")
COMPANIES = BUSINESS_TYPES[:3]
OVERLAP = 0.2            # subject similarity above which two draft rows are worth a question
OVERLAP_HEAD = 0.6       # the same for the subject outside brackets ("ICT Equipment (...)")
DUPLICATE = 0.92         # text similarity above which two draft rows are the same category
_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "etc",
          "per", "via", "into", "including", "e.g.", "i.e."}
_NUMBER_HEAD = re.compile(r"^(no\.?|#|s/?n|ref(erence)?|code|category code|cat\.? no\.?)$", re.I)
_DESC_HEAD = re.compile(r"description", re.I)
_PEOPLE = re.compile(r"consult|training|trainer|facilitat|translat|interpret|research|psycho|counsel|"
                     r"clinical|legal|audit|advis|evaluat|photograph|videograph|writer|editing", re.I)
_ONLY = re.compile(r"\(([^()]*?)\b(registered|certified|accredited|licensed)\b[^()]*\bonly\)", re.I)


# --- the draft ----------------------------------------------------------------

@dataclass
class Draft:
    id: str                         # the draft's own number, or "r<row>" when it has none
    description: str
    group: Optional[str] = None
    # What the client's draft states beyond the description (spec 07 R1), in its own columns
    sub_categories: List[str] = field(default_factory=list)
    licence: List[str] = field(default_factory=list)      # e.g. "min G5/BC5 or above"
    hr: List[str] = field(default_factory=list)           # key staff, e.g. "Site engineer/Residential Engineer"
    technical: List[str] = field(default_factory=list)    # e.g. "MAF/DAF", "(Mixer, Pick up, Dump Truck)"
    individual: bool = False        # the row says Individual Consultants may apply
    notes: List[str] = field(default_factory=list)        # questions for the client found while reading


def _number(text) -> Optional[str]:
    """'52' -> '52'; 'NRC/2026-27/014' -> '14'; text without a number -> None."""
    m = re.search(r"(\d+)\s*$", str(text or "").strip())
    return str(int(m.group(1))) if m else None


def _is_group_label(text: str) -> bool:
    """Capitals, small joining words aside: "ENGINEERING EQUIPMENTS and MATERIAL"."""
    letters = [c for w in text.split() if w.lower().strip(",.&()") not in _SMALL for c in w if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _header(ws) -> Tuple[int, int, int]:
    """(header row, number column, description column)."""
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 30)):
        cells = [c for c in row if c.value not in (None, "")]
        for i, c in enumerate(cells):
            if _NUMBER_HEAD.match(str(c.value).strip()):
                desc = next((d for d in cells[i + 1:] if _DESC_HEAD.search(str(d.value))), None) \
                    or (cells[i + 1] if i + 1 < len(cells) else None)
                if desc is not None:
                    return c.row, c.column, desc.column
    raise ValueError("no header row with a number column (No, #, Ref, Code) found")


# the optional columns a draft may add, by their header (spec 07 R1)
_EXTRA_HEADS = {"sub_categories": re.compile(r"^sub[\s-]*categor", re.I),
                "licence": re.compile(r"contractor\s*level|licen[cs]e|grade|registration", re.I),
                "hr": re.compile(r"^(hr\b|human\s*resource|key\s*staff|personnel)", re.I),
                "technical": re.compile(r"technical|equipment", re.I)}
_INDIVIDUAL = re.compile(r"individual\s+consultant", re.I)
_DOCUMENT = re.compile(r"certific|licen[cs]e|permit|registration", re.I)
_YEARS = re.compile(r"^(min(imum)?\.?|at\s+least)?\s*\d+\+?\s*(years?|yrs?)\b", re.I)


def split_items(text) -> List[str]:
    """A requirement cell as items (spec 07 R2): split on commas, semicolons
    and line breaks; a slash keeps alternatives together ("Site
    engineer/Residential Engineer"); "minimum 5 years" stays with the item it
    follows; a list in brackets is one item, "(Mixer, Pick up, Dump Truck)",
    closed when the client left it open."""
    pieces, buf, depth = [], "", 0
    for ch in str(text or ""):
        depth += (ch == "(") - (ch == ")" and depth > 0)
        if ch in ",;\n" and depth == 0:
            pieces.append(buf)
            buf = ""
        else:
            buf += ch
    pieces.append(buf)
    items = []
    for x in pieces:
        x = re.sub(r"\s*/\s*", "/", " ".join(x.split())).strip(" .")
        if not x:
            continue
        if x.startswith("("):
            x = "(" + x.strip("() ") + ")"
        if items and _YEARS.match(x):
            items[-1] += f" {x}"
        else:
            items.append(x)
    return items


def _extra_columns(ws, head: int, taken: Tuple[int, ...]) -> Dict[str, int]:
    out = {}
    for c in ws[head]:
        label = " ".join(str(c.value or "").split())
        if not label or c.column in taken:
            continue
        for name, pattern in _EXTRA_HEADS.items():
            if name not in out and pattern.search(label):
                out[name] = c.column
                break
    return out


def _add_extras(d: Draft, cells: Dict[str, str], row_text: str):
    """One row's requirement cells onto its category: the category's own row,
    or a continuation row under it (no number, no description)."""
    sub = " ".join(cells.get("sub_categories", "").split())
    if sub and _DOCUMENT.search(sub):
        d.licence.append(sub)
        d.notes.append(f"'{sub}' is in the sub-category column; it is asked as a licence document. Right?")
    elif sub:
        d.sub_categories.append(sub)
    for name in ("licence", "hr", "technical"):
        getattr(d, name).extend(split_items(cells.get(name)))
    d.individual = d.individual or bool(_INDIVIDUAL.search(row_text))
    for name in ("sub_categories", "licence", "hr", "technical", "notes"):
        setattr(d, name, list(dict.fromkeys(getattr(d, name))))


def _csv_draft(i: int, d: dict) -> Draft:
    parts = lambda name: [x.strip() for x in (d.get(name) or "").split("|") if x.strip()]
    return Draft(str(d.get("number") or f"r{i + 2}"), d["description"].strip(), d.get("group") or None,
                 parts("sub_categories"), parts("licence"), parts("hr"), parts("technical"),
                 INDIVIDUAL in (d.get("applies_to") or ""))


def read_draft(path: str, sheet: Optional[str] = None) -> List[Draft]:
    """A draft list as clients write it: a number column, a description
    column, and group rows (capitalised text without a number, in either
    column, merged or not); optionally sub-categories, licence, HR and
    technical columns, filled on the category's row or on rows under it
    (spec 07). A CSV categories table works too."""
    if path.lower().endswith(".csv"):
        with open(path, newline="") as fh:
            return [_csv_draft(i, d) for i, d in enumerate(csv.DictReader(fh))]
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    head, ncol, dcol = _header(ws)
    extra = _extra_columns(ws, head, (ncol, dcol))
    head_label = str(ws.cell(head, ncol).value).strip()
    first = str(ws.cell(head, dcol).value or "").strip()
    group = None if _DESC_HEAD.search(first) else first or None      # NRC: the header names the group
    out, seen = [], Counter()
    for r in range(head + 1, ws.max_row + 1):
        num_cell = str(ws.cell(r, ncol).value or "").strip()
        desc = " ".join(str(ws.cell(r, dcol).value or "").split())
        if num_cell == head_label:                     # a repeated header row naming the next group
            group = desc or group
            continue
        cells = {name: str(ws.cell(r, c).value or "").strip() for name, c in extra.items()}
        row_text = " ".join(str(c.value) for c in ws[r] if c.value not in (None, "") and c.column != dcol)
        number = _number(num_cell)
        if not number:
            label = desc or num_cell
            if not label and out and (any(cells.values()) or _INDIVIDUAL.search(row_text)):
                _add_extras(out[-1], cells, row_text)           # a continuation row of the category above
                continue
            if label and (not desc or _is_group_label(label)):
                group = label.strip()
                continue
            if not desc:
                continue
        seen[number] += 1
        out.append(Draft(number if number and seen[number] == 1 else f"r{r}", desc, group))
        _add_extras(out[-1], cells, row_text)
    return out


# --- wording --------------------------------------------------------------------

def title_case(text: str) -> str:
    """Title Case as the final lists write it: small words lower-case after the
    first, words already in capitals (ICT, CCTV, WASH) kept, hyphenated parts
    each capitalised."""
    out = []
    for i, word in enumerate(text.split()):
        lead = re.match(r"^[(\"']*", word).group()
        core = word[len(lead):]
        if not core or any(ch.isdigit() for ch in core) or (len(core) > 1 and core.isupper()):
            out.append(word)
        elif i and not lead and core.lower().strip(".,;:)") in _SMALL:
            out.append(core.lower())
        else:
            out.append(lead + "-".join(p[:1].upper() + p[1:] for p in core.split("-")))
    return " ".join(out)


def _same_text(a: str, b: str) -> float:
    norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", s.lower()))
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


# --- the proposal ---------------------------------------------------------------

@dataclass
class Line:
    description: str
    status: str
    from_draft: List[str]
    group: Optional[str] = None
    reason: str = ""
    question: str = ""
    sub_categories: List[str] = field(default_factory=list)
    eligibility: List[str] = field(default_factory=list)       # stated by the draft: applied
    applies_to: List[str] = field(default_factory=list)
    code: str = ""
    decision: str = ""
    comment: str = ""
    suggested_eligibility: List[str] = field(default_factory=list)   # the model's: shown, not applied
    technical_name: str = ""                                          # research (spec 05): suggested
    usual_split: List[str] = field(default_factory=list)              # research: how it is usually split
    research_sources: List[str] = field(default_factory=list)
    licence: List[str] = field(default_factory=list)          # the draft's requirement columns (spec 07)
    hr: List[str] = field(default_factory=list)
    technical: List[str] = field(default_factory=list)
    # Split and merge at review (spec 09): each piece becomes a category; merged lines become one
    split_into: List[str] = field(default_factory=list)        # with decision split
    merge_with: List[str] = field(default_factory=list)        # with decision merge: the revised list's # numbers
    suggested_split: List[str] = field(default_factory=list)   # by rules: shown, applied only when chosen
    suggested_merge: List[str] = field(default_factory=list)

    @property
    def gate_question(self) -> bool:
        return INDIVIDUAL in self.applies_to


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _SMALL}


def duplicates(drafts: List[Draft]) -> Dict[str, str]:
    """Draft id -> the earlier draft it repeats: the same words, near enough
    the same text. "Major Electrical Works" and "Minor Electrical Works" are
    92% alike but different categories."""
    out = {}
    for i, d in enumerate(drafts):
        for e in drafts[:i]:
            if e.id not in out and _words(d.description) == _words(e.description) and \
                    _same_text(d.description, e.description) >= DUPLICATE:
                out[d.id] = e.id
                break
    return out


_VERBS = re.compile(r"\b(supply|supplies|provision|provide|installation|install|maintenance|maintain|repairs?|"
                    r"delivery|deliver|servicing|services?|commission(?:ing)?|calibration|related|general|"
                    r"genuine|and|of|the|for|etc)\b", re.I)


def _subject(text: str, brackets: bool = True) -> str:
    """What a category buys, without the procurement verbs every row shares
    ("Supply, installation and maintenance of ...")."""
    if not brackets:
        text = re.sub(r"\([^)]*\)?", " ", text)
    return _VERBS.sub(" ", text)


def overlaps(drafts: List[Draft], skip: Dict[str, str]) -> List[Tuple[str, str, float]]:
    """Pairs of draft rows that may cover the same goods or services: similar
    subjects, judged on the whole description and on its head outside
    brackets, so long lists of examples do not hide "ICT Equipment" twice."""
    def vectors(brackets):
        texts = {d.id: _subject(d.description, brackets) for d in drafts}
        emb = TfidfEmbedder().fit(list(texts.values()))
        return {i: emb.vector(t) for i, t in texts.items()}
    full, head = vectors(True), vectors(False)
    out = []
    for i, a in enumerate(drafts):
        for b in drafts[i + 1:]:
            if a.id in skip or b.id in skip:
                continue
            s = max(cosine(full[a.id], full[b.id]),
                    cosine(head[a.id], head[b.id]) * OVERLAP / OVERLAP_HEAD)
            if s >= OVERLAP:
                out.append((a.id, b.id, s))
    return sorted(out, key=lambda x: -x[2])


def rule_applies_to(description: str) -> List[str]:
    """Individuals may apply for consultancy and other personal services."""
    if category_kind(description) == "consultancy" or _PEOPLE.search(description):
        return BUSINESS_TYPES[:]
    return COMPANIES[:]


_ONLY_ABBREVIATION = re.compile(r"\b([A-Z]{2,})\)?\s*[-\u2013]?\s*(registered|certified|accredited|licensed)\b"
                                r"[^()]*\bonly\b", re.I)


def rule_eligibility(description: str) -> List[str]:
    """'(IATA Registered Only)' -> 'IATA Registration Certificate';
    '(... (CALP)-Certified Trainers Only)' -> 'CALP Certificate'."""
    m = _ONLY_ABBREVIATION.search(description)
    if m and m.group(1).isupper():
        name = m.group(1)
        return [f"{name} Registration Certificate" if m.group(2).lower() == "registered" else f"{name} Certificate"]
    m = _ONLY.search(description)
    if not m:
        return []
    body = m.group(1).strip(" -")
    words = [w for w in body.split() if w[:1].isupper()] or body.split()
    name = " ".join(words[:3]).strip()
    return [f"{name} Registration Certificate" if m.group(2).lower() == "registered" else f"{name} Certificate"]


def rule_lines(drafts: List[Draft]) -> List[Line]:
    """The proposal without a model: duplicates folded, overlaps raised as
    questions, and the rule-based columns."""
    dups = duplicates(drafts)
    by_id = {d.id: d for d in drafts}
    asks = defaultdict(list)
    for a, b, _ in overlaps(drafts, dups):
        asks[b].append(f"Overlaps with #{a} ({by_id[a].description}): merge them, or keep both?")
    lines = []
    for d in drafts:
        if d.id in dups:
            lines.append(Line(d.description, REPEATED, [d.id], d.group, f"same as #{dups[d.id]}"))
        else:
            lines.append(Line(d.description, KEPT, [d.id], d.group, question=" ".join(asks[d.id])))
    return lines


# --- the model ------------------------------------------------------------------

SYSTEM = """You refine a draft list of procurement categories for a supplier prequalification, as an
experienced procurement team would before sending it to the client for approval.

For every draft row decide one status:
- Kept: fine as it is.
- Renamed: same category, wording fixed (spelling, clarity). Give the new wording.
- Merged: two or more rows cover the same goods or services; one category replaces them. Give the reason.
- Separated: one row bundles what different kinds of supplier provide; give one line per new category,
  each with the same "from", and the reason. A category should be something one kind of specialist
  supplier provides: separate a row that combines distinct trades (e.g. catering with event decoration,
  vehicle hire with vehicle repair), goods with construction works, or a service with the installation of
  systems for it. Keep together items one supplier normally provides (e.g. stationery and toner).
- Repeated: the row is a duplicate of another row.
Never remove a category: scope is the client's decision. Where you are unsure, keep the row and write a
question for the client instead (e.g. "Any specific languages?").

The number of categories has a business cost both ways: fewer means less income, more means more work for
suppliers. Aim for a middle ground; merge or separate only with a clear reason.

Write descriptions in Title Case, starting "Supply of", "Provision of" or "Supply and Installation of" as the
draft does. Keep the client's groups.

For each line also give, where they apply:
- sub_categories: distinct items the client may want suppliers to choose between (only if the description
  lists clearly different product or service lines);
- eligibility: documents a supplier must hold to be eligible, e.g. "IATA Registration Certificate" for
  "(IATA Registered Only)";
- applies_to: which business types may apply: Sole Proprietor, Private Limited Company, Share Company, and
  Individual Consultant only where an individual can deliver the service (consultancy, training,
  translation, professional services).

Under "additional", suggest categories common in comparable past exercises (listed below) that this
client's draft lacks, each with its reason and the group it belongs to. Suggest few, only clear gaps.

When the client's brief is given, it comes first. Set from_brief on a line or addition you made because of
it; never remove a category for it either."""

SCHEMA = {"type": "object", "required": ["lines", "additional"], "properties": {
    "lines": {"type": "array", "items": {"type": "object", "required": ["from", "status", "description"],
                                         "properties": {
        "from": {"type": "array", "items": {"type": "string"}},
        "status": {"type": "string", "enum": [KEPT, RENAMED, MERGED, SEPARATED, REPEATED]},
        "description": {"type": "string"}, "reason": {"type": "string"}, "question": {"type": "string"},
        "sub_categories": {"type": "array", "items": {"type": "string"}},
        "eligibility": {"type": "array", "items": {"type": "string"}},
        "applies_to": {"type": "array", "items": {"type": "string", "enum": BUSINESS_TYPES}},
        "from_brief": {"type": "boolean"}}}},
    "additional": {"type": "array", "items": {"type": "object", "required": ["description", "reason"],
                                              "properties": {"description": {"type": "string"},
                                                             "group": {"type": "string"},
                                                             "reason": {"type": "string"},
                                                             "from_brief": {"type": "boolean"}}}}}}


def build_prompt(drafts: List[Draft], past: List[str], target: Optional[Tuple[int, int]], brief: str = "") -> str:
    dups = duplicates(drafts)
    lines = cb.prompt_lines(brief) + [f"DRAFT ({len(drafts)} rows; id | group | description):"]
    lines += [f"  {d.id} | {d.group or '-'} | {d.description}" for d in drafts]
    lines += ["", "FOUND BY CODE (check them):"]
    lines += [f"  {a} looks like a duplicate of {b}" for a, b in dups.items()]
    lines += [f"  {a} and {b} may overlap" for a, b, _ in overlaps(drafts, dups)[:30]]
    if target:
        lines += ["", f"TARGET: about {target[0]}-{target[1]} categories (a guide, not a rule)."]
    if past:
        lines += ["", "CATEGORIES IN COMPARABLE PAST EXERCISES:"] + [f"  {p}" for p in past[:150]]
    return "\n".join(lines)


class ModelRefiner:
    def __init__(self, llm=None, model: Optional[str] = None):
        self.llm = llm or make_llm(model=model)

    def propose(self, drafts: List[Draft], client: str, past: List[str],
                target: Optional[Tuple[int, int]], brief: str = "") -> Optional[dict]:
        try:
            return self.llm.complete_json(SYSTEM, build_prompt(drafts, past, target, brief), SCHEMA,
                                          "category_refinement", client=client, max_tokens=32000)
        except LLMError as e:
            log.info(f"refine: no model proposal ({e}); proposing by rules")
            return None

    def interpret(self, comments: List[Tuple[Line, str]], client: str) -> List[str]:
        """A change proposed for each free-text client comment (never applied)."""
        if not comments:
            return []
        prompt = "Turn each client comment on a category into one proposed change to the category list.\n" + \
            "\n".join(f"  {i} | {line.description} | comment: {c}" for i, (line, c) in enumerate(comments))
        schema = {"type": "object", "required": ["changes"], "properties": {"changes": {"type": "array",
                  "items": {"type": "object", "required": ["id", "change"],
                            "properties": {"id": {"type": "integer"}, "change": {"type": "string"}}}}}}
        try:
            raw = self.llm.complete_json("You help a procurement team act on a client's review comments.",
                                         prompt, schema, "category_comments", client=client, max_tokens=8000)
            got = {a["id"]: a["change"] for a in raw["changes"]}
        except LLMError as e:
            log.info(f"refine: comments not interpreted ({e})")
            got = {}
        return [got.get(i, "") for i in range(len(comments))]


# --- checking the proposal ------------------------------------------------------

def _clean(xs) -> List[str]:
    return [" ".join(str(x).split()) for x in xs or [] if str(x).strip()]


def _line_from_model(raw: dict, by_id: Dict[str, Draft], dups: Dict[str, str]) -> List[Line]:
    """One model line as checked lines (a refused merge becomes one per row)."""
    ids = [i for i in dict.fromkeys(str(x) for x in raw.get("from", [])) if i in by_id]
    if not ids:
        return []
    status, reason = raw["status"], cb.labelled(raw.get("reason"), raw.get("from_brief"))
    d0 = by_id[ids[0]]
    line = Line(" ".join(raw["description"].split()) or d0.description, status, ids, d0.group, reason,
                (raw.get("question") or "").strip(), _clean(raw.get("sub_categories")), [],
                [b for b in raw.get("applies_to") or [] if b in BUSINESS_TYPES],
                suggested_eligibility=_clean(raw.get("eligibility")))
    if status == MERGED and (len(ids) < 2 or not reason):
        return [Line(by_id[i].description, KEPT, [i], by_id[i].group,
                     question=f"Merge with #{', #'.join(x for x in ids if x != i)} into "
                              f"'{line.description}'?" if len(ids) > 1 else "") for i in ids]
    if status in (RENAMED, KEPT, REPEATED, SEPARATED) and len(ids) > 1:
        line.from_draft = ids = ids[:1]
    if status == REPEATED and ids[0] not in dups:
        twin = max((d for d in by_id.values() if d.id != ids[0]),
                   key=lambda d: _same_text(d.description, d0.description), default=None)
        if not twin or _same_text(twin.description, d0.description) < DUPLICATE:
            line.status, line.description = KEPT, d0.description
            line.question = (line.question + " " if line.question else "") + \
                "Is this a duplicate of another category?"
    if status == KEPT:
        line.description = d0.description
    if status == RENAMED:
        line.reason = (reason + "; " if reason else "") + f"was: {d0.description}"
    if status == SEPARATED and not reason:
        line.status, line.description = KEPT, d0.description
        line.question = (line.question + " " if line.question else "") + f"Separate into '{raw['description']}'?"
    return [line]


def _settle_eligibility(ln: Line, by_id: Dict[str, Draft]):
    """Eligibility is a pass/fail upload in every sheet of the category, so
    only what the draft itself states is applied ("(IATA Registered Only)");
    other documents the model proposes are shown as suggestions."""
    stated = list(dict.fromkeys(e for text in [ln.description] + [by_id[i].description for i in ln.from_draft
                                                                    if i in by_id]
                                for e in rule_eligibility(text)))
    ln.eligibility = ln.eligibility or stated
    have = {e.lower() for e in ln.eligibility}
    ln.suggested_eligibility = [e for e in ln.suggested_eligibility if e.lower() not in have]


def _carry_draft(ln: Line, by_id: Dict[str, Draft]):
    """What the draft rows state beyond their description, on the line made
    from them: their sub-categories (over the model's), licence, HR and
    technical items, Individual Consultant, and the questions found reading
    them (spec 07 R3)."""
    rows = [by_id[i] for i in ln.from_draft if i in by_id]
    union = lambda name: list(dict.fromkeys(x for d in rows for x in getattr(d, name)))
    ln.sub_categories = union("sub_categories") or ln.sub_categories
    for name in ("licence", "hr", "technical"):
        setattr(ln, name, list(dict.fromkeys(getattr(ln, name) + union(name))))
    if any(d.individual for d in rows) and INDIVIDUAL not in ln.applies_to:
        ln.applies_to = [b for b in BUSINESS_TYPES if b in ln.applies_to + [INDIVIDUAL]]
    notes = [n for n in union("notes") if n not in ln.question]
    ln.question = " ".join([ln.question] + notes).strip()


def check(raw: Optional[dict], drafts: List[Draft], groups_order: List[Optional[str]]) -> List[Line]:
    """The model's proposal as lines the code stands behind (or the rules'
    proposal when there is none). Every draft row ends up on a line."""
    by_id = {d.id: d for d in drafts}
    dups = duplicates(drafts)
    lines = rule_lines(drafts) if raw is None else \
        [ln for r in raw.get("lines", []) for ln in _line_from_model(r, by_id, dups)]
    # a "separation" into one line is a rename
    per_id = Counter(i for ln in lines if ln.status == SEPARATED for i in ln.from_draft)
    for ln in lines:
        if ln.status == SEPARATED and per_id[ln.from_draft[0]] < 2:
            ln.status, ln.reason = RENAMED, f"{ln.reason}; was: {by_id[ln.from_draft[0]].description}"
    covered = {i for ln in lines for i in ln.from_draft}
    for d in drafts:
        if d.id not in covered:
            lines.append(Line(d.description, KEPT, [d.id], d.group,
                              question="(not reviewed by the model)" if raw is not None else ""))
    if raw is not None:
        known = {d.description.lower() for d in drafts}
        names = {(g or "").lower(): g for g in groups_order}
        for a in raw.get("additional", []):
            desc = " ".join(a["description"].split())
            if desc and desc.lower() not in known and \
                    max((_same_text(desc, d.description) for d in drafts), default=0) < 0.8:
                lines.append(Line(desc, ADDITIONAL, [], names.get((a.get("group") or "").lower()),
                                  cb.labelled(a["reason"], a.get("from_brief"))))
    for ln in lines:
        new = title_case(ln.description)
        if new != ln.description and ln.status == KEPT:
            ln.status, ln.reason = RENAMED, f"capitalisation; was: {ln.description}"
        ln.description = new
        ln.applies_to = [b for b in BUSINESS_TYPES if b in (ln.applies_to or rule_applies_to(ln.description))]
        _carry_draft(ln, by_id)
        _settle_eligibility(ln, by_id)
    order = {d.id: i for i, d in enumerate(drafts)}
    gpos = {g: i for i, g in enumerate(groups_order)}
    lines.sort(key=lambda ln: (gpos.get(ln.group, len(gpos)),
                               min((order[i] for i in ln.from_draft), default=len(order))))
    suggest(lines)
    return lines


_HEAD = re.compile(r"^(.*?\bof)\s", re.I)
_TWO_HEADS = re.compile(r"\s+and\s+(?=(?:supply|provision)(?:\s+and\s+\w+)?\s+of\b)", re.I)


def suggest(lines: List[Line]):
    """Split and merge candidates by rules (spec 09 S2), on each line by the
    revised list's # numbers; never applied unless chosen."""
    live = [(i, ln) for i, ln in enumerate(lines) if ln.status != REPEATED]
    for _, ln in live:
        ln.suggested_split, ln.suggested_merge = [], []
        head = _HEAD.match(ln.description)
        if len(ln.sub_categories) >= 2:
            ln.suggested_split = [title_case(f"{head.group(1)} {s}" if head else s) for s in ln.sub_categories]
        else:
            parts = [p.strip(" ,") for p in _TWO_HEADS.split(ln.description) if p.strip(" ,")]
            if len(parts) >= 2:
                ln.suggested_split = [title_case(p) for p in parts]
    drafts = [Draft(str(i), ln.description) for i, ln in live]
    for a, b, _ in overlaps(drafts, {}):
        ia, ib = int(a), int(b)
        lines[ia].suggested_merge.append(str(ib + 1))
        lines[ib].suggested_merge.append(str(ia + 1))


def traceability(lines: List[Line], drafts: List[Draft]) -> List[str]:
    """Spec 01 R10: every draft row has a line; every line comes from the
    draft unless it is a suggested addition."""
    problems = []
    covered = {i for ln in lines for i in ln.from_draft}
    problems += [f"draft #{d.id} ({d.description}) has no line" for d in drafts if d.id not in covered]
    problems += [f"'{ln.description}' comes from no draft row" for ln in lines
                 if not ln.from_draft and ln.status != ADDITIONAL]
    return problems


# --- workbooks ------------------------------------------------------------------

REVISED_HEADERS = ["#", "Category Code", "Category Description", "Status", "From draft #", "Reason",
                   "Question for client", "Sub-categories", "Eligibility", "Licence", "HR", "Technical",
                   "Suggested eligibility (not applied)",
                   "Technical name (suggested)", "Usually split as", "Research sources",
                   "Gate Question", "Applies To", "Decision", "Client comment", "Split into", "Merge with #",
                   "Suggested split", "Suggested merge with #", "Group"]
DRAFT_HEADERS = ["Draft #", "Group", "Category Description", "Sub-categories", "Licence", "HR", "Technical",
                 "Individual Consultant", "Notes"]
HEAD = PatternFill("solid", fgColor="FFC5D9F1")
GROUP = PatternFill("solid", fgColor="FFDCE6F1")
ANSWER = PatternFill("solid", fgColor="FFFFF2CC")


def groups_of(drafts: List[Draft]) -> List[Optional[str]]:
    return list(dict.fromkeys(d.group for d in drafts))


def _write_lines(ws, lines: List[Line], headers: List[str], values, widths: List[float]):
    ws.append(headers)
    for c, w in zip(ws[1], widths):
        c.font, c.fill = Font(bold=True), HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[c.column_letter].width = w
    group, n = object(), 0
    for ln in lines:
        if ln.group != group:
            group = ln.group
            ws.append([group or "OTHER"])
            r = ws.max_row
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(headers) - 1)
            ws.cell(r, 1).font, ws.cell(r, 1).fill = Font(bold=True), GROUP
        n += 1
        ws.append(values(n, ln))
        for c in ws[ws.max_row]:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "D2"


def write_revised(path: str, drafts: List[Draft], lines: List[Line], client: str,
                  target: Optional[Tuple[int, int]] = None, brief: str = "", brief_applied: bool = False):
    problems = traceability(lines, drafts)
    if problems:
        raise ValueError("revised list not written: " + "; ".join(problems))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Draft"
    ws.append(DRAFT_HEADERS)
    for d in drafts:
        ws.append([d.id, d.group, d.description, " | ".join(d.sub_categories), " | ".join(d.licence),
                   " | ".join(d.hr), " | ".join(d.technical), "TRUE" if d.individual else "", "\n".join(d.notes)])
    for c, w in zip(ws[1], [9, 34, 100, 36, 28, 36, 36, 12, 40]):
        c.font, c.fill = Font(bold=True), HEAD
        ws.column_dimensions[c.column_letter].width = w

    ws = wb.create_sheet("Revised")
    _write_lines(ws, lines, REVISED_HEADERS, lambda n, ln: [
        n, ln.code, ln.description, ln.status, ", ".join(ln.from_draft), ln.reason, ln.question,
        " | ".join(ln.sub_categories), " | ".join(ln.eligibility), " | ".join(ln.licence), " | ".join(ln.hr),
        " | ".join(ln.technical), " | ".join(ln.suggested_eligibility), ln.technical_name, " | ".join(ln.usual_split), "\n".join(ln.research_sources),
        "TRUE" if ln.gate_question else "", ", ".join(ln.applies_to), ln.decision, ln.comment,
        " | ".join(ln.split_into), ", ".join(ln.merge_with), " | ".join(ln.suggested_split),
        ", ".join(ln.suggested_merge), ln.group or ""],
        [5, 13, 60, 20, 10, 40, 40, 36, 28, 28, 36, 36, 34, 40, 40, 50, 9, 34, 11, 30, 40, 12, 40, 14, 4])
    answers = [REVISED_HEADERS.index(h) for h in ("Decision", "Client comment", "Split into", "Merge with #")]
    for row in ws.iter_rows(min_row=2):
        if row[3].value:                                   # a line, not a group row
            for c in answers:
                row[c].fill = ANSWER
    ws.column_dimensions[openpyxl.utils.get_column_letter(len(REVISED_HEADERS))].hidden = True     # Group

    ws = wb.create_sheet("Summary")
    kept = [ln for ln in lines if ln.status != REPEATED]
    rows = [["Client", client], ["Draft rows", len(drafts)], ["Proposed categories", len(kept)]]
    if target:
        rows.append(["Target", f"{target[0]}-{target[1]}"])
    if brief:
        rows += [["Client's brief", brief],
                 ["", "read first by the model" if brief_applied else cb.NOT_APPLIED]]
    rows += [[], ["Status", "Lines"]] + [[s, n] for s, n in Counter(ln.status for ln in lines).most_common()]
    rows += [[], ["Group", "Draft rows", "Proposed"]]
    for g in groups_of(drafts) + ([None] if any(ln.group is None for ln in lines) else []):
        rows.append([g or "OTHER", sum(d.group == g for d in drafts), sum(ln.group == g for ln in kept)])
    rows += [[], ["How to answer: the Decision column of the Revised tab"],
             ["blank / accept", "as proposed"], ["reject", "back to the draft row(s)"],
             ["remove", "the category is dropped"], ["edit", "use the wording typed in Client comment"],
             ["split", "each piece in Split into (separated by |) becomes a category of its own; "
                       "Suggested split has a ready-made one to copy"],
             ["merge", "the lines whose # are in Merge with # (e.g. 13, 19) become one category with this "
                       "line; it keeps this wording, or the one typed in Client comment"],
             ["", "A Client comment without a decision is a note: it is turned into a proposed change for "
                  "the team, never applied on its own. Other cells (Eligibility, Applies To...) may be "
                  "edited directly."],
             ["Eligibility", "Only what the draft states is applied: every document there becomes a mandatory "
                             "pass/fail upload in the category's sheet. 'Suggested eligibility' lists documents "
                             "proposed beyond the draft; copy any the client wants into Eligibility."],
             ["Research", "Technical name, Usually split as and Research sources come from past exercises "
                          "first, then a web search, with their sources. They are suggestions: to use a name, "
                          "answer edit and type it in Client comment; to split, edit the line or add one."]]
    for r in rows:
        ws.append(r)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 60
    wb.save(path)


def _split(text, sep: str) -> List[str]:
    return [x.strip() for x in str(text or "").split(sep) if x.strip()]


def read_revised(path: str) -> Tuple[List[Draft], List[Line]]:
    wb = openpyxl.load_workbook(path)
    ws = wb["Draft"]
    dcol = {c.value: i for i, c in enumerate(ws[1])}
    drafts = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[0] is None:
            continue
        g = lambda name: r[dcol[name]] if name in dcol and dcol[name] < len(r) else None
        drafts.append(Draft(str(r[0]), str(r[2] or ""), r[1] or None, _split(g("Sub-categories"), "|"),
                            _split(g("Licence"), "|"), _split(g("HR"), "|"), _split(g("Technical"), "|"),
                            str(g("Individual Consultant") or "").strip().upper() == "TRUE",
                            _split(g("Notes"), "\n")))
    ws = wb["Revised"]
    col = {c.value: i for i, c in enumerate(ws[1])}
    lines = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[col["Status"]]:
            continue
        g = lambda name: r[col[name]] if name in col and col[name] < len(r) else None
        lines.append(Line(str(g("Category Description") or "").strip(), str(g("Status")).strip(),
                          _split(g("From draft #"), ","), g("Group") or None, str(g("Reason") or ""),
                          str(g("Question for client") or ""), _split(g("Sub-categories"), "|"),
                          _split(g("Eligibility"), "|"),
                          [b for b in _split(g("Applies To"), ",") if b in BUSINESS_TYPES],
                          str(g("Category Code") or "").strip(), str(g("Decision") or "").strip().lower(),
                          str(g("Client comment") or "").strip(),
                          _split(g("Suggested eligibility (not applied)"), "|"),
                          str(g("Technical name (suggested)") or "").strip(), _split(g("Usually split as"), "|"),
                          _split(g("Research sources"), "\n"), _split(g("Licence"), "|"), _split(g("HR"), "|"),
                          _split(g("Technical"), "|"), _split(g("Split into"), "|"),
                          [n.lstrip("#") for n in _split(str(g("Merge with #") or ""), ",")],
                          _split(g("Suggested split"), "|"),
                          [n.lstrip("#") for n in _split(str(g("Suggested merge with #") or ""), ",")]))
    return drafts, lines


def attach_research(lines: List[Line], table, country: str):
    """Research findings (spec 05) on each proposed category: a technical
    name and its usual split, and licences added to Suggested eligibility -
    each with its sources; none is applied."""
    from . import research as rs
    from .research import describe
    for ln in lines:
        if ln.status == REPEATED:
            continue
        name, lic = table.lookup("names", ln.description, country), table.lookup("licences", ln.description, country)
        sources = []
        if name:
            proposed = name.proposal.get("technical_name", "")
            if proposed and _words(proposed) != _words(ln.description):
                ln.technical_name = proposed
            ln.usual_split = list(name.proposal.get("usually_split_as") or [])
            sources += [f"name: {x}" for x in name.sources] or ([f"name: {rs.NO_SOURCE}"] if proposed else [])
        if lic:
            have = {e.lower() for e in ln.eligibility + ln.suggested_eligibility}
            ln.suggested_eligibility += [x["name"] for x in lic.proposal.get("licences", [])
                                         if x.get("name") and x["name"].lower() not in have]
            sources += [f"licences ({describe(lic)[:80]}): {x}" for x in lic.sources] or \
                ([f"licences ({describe(lic)[:80]}): {rs.NO_SOURCE}"] if lic.proposal.get("licences") else [])
        ln.research_sources = list(dict.fromkeys(sources))


# --- step B: apply the review ---------------------------------------------------

def _original(d: Draft) -> Line:
    ln = Line(title_case(d.description), KEPT, [d.id], d.group, "decision: reject (back to the draft row)",
              applies_to=rule_applies_to(d.description), eligibility=rule_eligibility(d.description))
    _carry_draft(ln, {d.id: d})
    return ln


def _merges(lines: List[Line], problems: List[str]) -> Dict[int, int]:
    """Line index -> the index of the line it is merged into (spec 09 S1). A
    merge naming a # that does not exist, itself, or a line already merged
    elsewhere changes nothing and is a problem."""
    into: Dict[int, int] = {}
    for i, ln in enumerate(lines):
        if ln.decision != "merge":
            continue
        nums = [str(n).strip().lstrip("#") for n in ln.merge_with if str(n).strip()]
        js, bad = [], None
        if i in into:
            bad = f"it is itself merged into #{into[i] + 1}"
        for n in nums:
            j = int(n) - 1 if n.isdigit() else -1
            if not 0 <= j < len(lines):
                bad = bad or f"#{n} is not a line of the revised list"
            elif j == i:
                bad = bad or "a line cannot merge with itself"
            elif j in into or j in js or lines[j].decision == "merge" or j in into.values():
                bad = bad or f"#{n} is already in another merge"
            else:
                js.append(j)
        if not nums:
            bad = "no # numbers in Merge with #"
        if bad:
            problems.append(f"'{ln.description}': merge not applied: {bad}; kept as it is")
            ln.decision = ""
            continue
        for j in js:
            into[j] = i
    return into


def _union(lines: List[Line], name: str) -> List[str]:
    return list(dict.fromkeys(x for ln in lines for x in getattr(ln, name)))


def _merged(ln: Line, others: List[Line]) -> Line:
    """One category from the line and the lines merged into it."""
    every = [ln] + others
    out = Line(ln.comment or ln.description, MERGED, _union(every, "from_draft"), ln.group,
               f"merged at review with {', '.join(o.description for o in others)}"
               + (f"; was: {ln.description}" if ln.comment else ""), ln.question,
               _union(every, "sub_categories"), _union(every, "eligibility"),
               [b for b in BUSINESS_TYPES if b in _union(every, "applies_to")], ln.code,
               licence=_union(every, "licence"), hr=_union(every, "hr"), technical=_union(every, "technical"))
    return out


def _same(a: str, b: str) -> bool:
    na, nb = " ".join(_words(a)), " ".join(_words(b))
    return bool(na and nb) and (na in nb or nb in na)


def _pieces(ln: Line) -> List[Line]:
    """Each piece a category of its own. Split by its sub-categories, a piece
    named after one carries none; other pieces keep those left over."""
    names = list(dict.fromkeys(title_case(" ".join(p.split())) for p in ln.split_into if p.strip()))
    used = {s for s in ln.sub_categories for p in names if _same(s, p)}
    left = [s for s in ln.sub_categories if s not in used]
    return [Line(p, SEPARATED, ln.from_draft[:], ln.group, f"split at review; was: {ln.description}", "",
                 [] if any(_same(s, p) for s in ln.sub_categories) else left, ln.eligibility[:], ln.applies_to[:],
                 licence=ln.licence[:], hr=ln.hr[:], technical=ln.technical[:]) for p in names]


def apply_review(drafts: List[Draft], lines: List[Line]) -> Tuple[List[Line], List[Line], List[str]]:
    """Returns (final lines, excluded lines, problems)."""
    by_id = {d.id: d for d in drafts}
    final, excluded, problems, undone = [], [], [], set()
    for ln in lines:
        if ln.decision == "split" and len({p.strip().lower() for p in ln.split_into if p.strip()}) < 2:
            problems.append(f"'{ln.description}': split not applied: give two or more pieces in Split into; "
                            f"kept as it is")
            ln.decision = ""
    into = _merges(lines, problems)
    folded = defaultdict(list)
    for j, i in into.items():
        folded[i].append(lines[j])
    undone_separations = {ln.from_draft[0] for ln in lines if ln.status == SEPARATED and ln.decision == "reject"}
    for index, ln in enumerate(lines):
        d = ln.decision
        if d not in DECISIONS:
            problems.append(f"'{ln.description}': decision '{ln.decision}' is not accept, reject, remove, edit, "
                            f"split or merge; taken as accept")
            d = ""
        if index in into:
            ln.reason = f"merged into #{into[index] + 1} at review"
            excluded.append(ln)
            continue
        if d == "merge":
            final.append(_merged(ln, sorted(folded[index], key=lines.index)))
            continue
        if d == "split":
            final += _pieces(ln)
            continue
        if ln.status == SEPARATED and ln.from_draft[0] in undone_separations:
            if ln.from_draft[0] not in undone:
                undone.add(ln.from_draft[0])
                final.append(_original(by_id[ln.from_draft[0]]))
            excluded.append(ln)
        elif d == "remove":
            ln.status = REMOVED
            excluded.append(ln)
        elif d == "reject":
            excluded.append(ln)
            if ln.status in (RENAMED, MERGED, REPEATED):
                final += [_original(by_id[i]) for i in ln.from_draft if i in by_id]
            elif ln.status == KEPT:
                final.append(ln)
        elif d == "edit":
            if not ln.comment:
                problems.append(f"'{ln.description}': edit without new wording in Client comment; kept as is")
            else:
                ln.reason, ln.description, ln.comment = f"edited at review; was: {ln.description}", ln.comment, ""
            final.append(ln)
        elif ln.status == REPEATED:
            excluded.append(ln)
        else:
            final.append(ln)
    for ln in final:
        ln.applies_to = [b for b in BUSINESS_TYPES if b in ln.applies_to] or rule_applies_to(ln.description)
    gpos = {g: i for i, g in enumerate(groups_of(drafts))}
    final.sort(key=lambda ln: gpos.get(ln.group, len(gpos)))          # stable: keeps the review's order
    covered = {i for ln in final + excluded for i in ln.from_draft}
    problems += [f"draft #{d.id} ({d.description}) is in no line" for d in drafts if d.id not in covered]
    return final, excluded, problems


FINAL_HEADERS = ["#", "Category Code", "Category Description", "Sub-categories", "Eligibility",
                 "Licence", "HR", "Technical", "Gate Question", "Applies To", "From draft #", "Group"]


def write_final(path: str, final: List[Line], notes: List[Tuple[Line, str, str]]):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Final Categories"
    _write_lines(ws, final, FINAL_HEADERS, lambda n, ln: [
        n, ln.code, ln.description, " | ".join(ln.sub_categories), " | ".join(ln.eligibility),
        " | ".join(ln.licence), " | ".join(ln.hr), " | ".join(ln.technical),
        "TRUE" if ln.gate_question else "", ", ".join(ln.applies_to), ", ".join(ln.from_draft), ln.group or ""],
        [5, 13, 70, 36, 28, 28, 36, 36, 9, 34, 10, 4])
    ws.column_dimensions[openpyxl.utils.get_column_letter(len(FINAL_HEADERS))].hidden = True      # Group
    if notes:
        ws = wb.create_sheet("Client comments")
        ws.append(["Category", "Client comment", "Proposed change (not applied)"])
        for c, w in zip(ws[1], [60, 50, 60]):
            c.font, c.fill = Font(bold=True), HEAD
            ws.column_dimensions[c.column_letter].width = w
        for ln, comment, change in notes:
            ws.append([ln.description, comment, change])
    wb.save(path)


def to_categories(final: List[Line], client: str, version: Optional[str] = None) -> List[Category]:
    return [Category(i, ln.description, ln.group, ln.sub_categories, client, version, "", ln.eligibility,
                     ln.gate_question, ln.applies_to, ln.licence, ln.hr, ln.technical)
            for i, ln in enumerate(final, 1)]


# --- evaluation against a team's own revision -----------------------------------

def evaluate(drafts: List[Draft], lines: List[Line], team_final: List[str], match: float = 0.5) -> Dict:
    """How the proposal compares with the list the team and client settled on
    (spec 01, NRC history): which of their categories it proposed, which of
    its proposals they did not make, and their separations and removals."""
    proposed = [ln for ln in lines if ln.status != REPEATED]
    emb = TfidfEmbedder().fit([d.description for d in drafts] + team_final + [ln.description for ln in proposed])
    v = emb.vector
    best = lambda text, pool: max((cosine(v(text), v(p)) for p in pool), default=0.0)
    found = [t for t in team_final if best(t, [ln.description for ln in proposed]) >= match]
    extra = [ln for ln in proposed if best(ln.description, team_final) < match]
    home = Counter()
    for t in team_final:
        scores = [(cosine(v(t), v(d.description)), d.id) for d in drafts]
        s, i = max(scores)
        if s >= 0.3:
            home[i] += 1
    team_split = {i for i, n in home.items() if n >= 2}
    team_dropped = {d.id for d in drafts} - set(home)
    ours_split = {ln.from_draft[0] for ln in lines if ln.status == SEPARATED}
    ours_merged = {i for ln in lines if ln.status == MERGED for i in ln.from_draft}
    return {"draft rows": len(drafts), "team final": len(team_final), "proposed": len(proposed),
            "team categories proposed": f"{len(found)}/{len(team_final)}",
            "proposals the team did not make": [ln.description for ln in extra],
            "team separations proposed": f"{len(team_split & ours_split)}/{len(team_split)}",
            "separations the team did not make": sorted(ours_split - team_split, key=str),
            "rows the team dropped (removed or merged)": len(team_dropped),
            "of those, merged or folded by the proposal": len(team_dropped & (ours_merged | {
                i for ln in lines if ln.status == REPEATED for i in ln.from_draft}))}
