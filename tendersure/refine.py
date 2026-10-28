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
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

from .categories import Category, category_kind
from .llm import LLMError, make_llm
from .similarity import TfidfEmbedder, cosine

KEPT, RENAMED, MERGED, SEPARATED, REPEATED = "Kept", "Renamed", "Merged", "Separated", "Repeated"
ADDITIONAL, REMOVED = "Additional (suggested)", "Removed"
STATUSES = [KEPT, RENAMED, MERGED, SEPARATED, REPEATED, ADDITIONAL, REMOVED]
COMPANIES = ["Sole Proprietor", "Private Limited Company", "Share Company"]
INDIVIDUAL = "Individual Consultant"
BUSINESS_TYPES = COMPANIES + [INDIVIDUAL]
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


def _number(text) -> Optional[str]:
    """'52' -> '52'; 'NRC/2026-27/014' -> '14'; text without a number -> None."""
    m = re.search(r"(\d+)\s*$", str(text or "").strip())
    return str(int(m.group(1))) if m else None


def _is_group_label(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
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


def read_draft(path: str, sheet: Optional[str] = None) -> List[Draft]:
    """A draft list as clients write it: a number column, a description
    column, and group rows (capitalised text without a number, in either
    column, merged or not). A CSV categories table works too."""
    if path.lower().endswith(".csv"):
        with open(path, newline="") as fh:
            return [Draft(str(d.get("number") or f"r{i + 2}"), d["description"].strip(), d.get("group") or None)
                    for i, d in enumerate(csv.DictReader(fh))]
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    head, ncol, dcol = _header(ws)
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
        number = _number(num_cell)
        if not number:
            label = desc or num_cell
            if label and (not desc or _is_group_label(label)):
                group = label.strip()
                continue
            if not desc:
                continue
        seen[number] += 1
        out.append(Draft(number if number and seen[number] == 1 else f"r{r}", desc, group))
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

    @property
    def gate_question(self) -> bool:
        return INDIVIDUAL in self.applies_to


def duplicates(drafts: List[Draft]) -> Dict[str, str]:
    """Draft id -> the earlier draft it repeats."""
    out = {}
    for i, d in enumerate(drafts):
        for e in drafts[:i]:
            if e.id not in out and _same_text(d.description, e.description) >= DUPLICATE:
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


def rule_eligibility(description: str) -> List[str]:
    """'(IATA Registered Only)' -> 'IATA Registration Certificate'."""
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
client's draft lacks, each with its reason and the group it belongs to. Suggest few, only clear gaps."""

SCHEMA = {"type": "object", "required": ["lines", "additional"], "properties": {
    "lines": {"type": "array", "items": {"type": "object", "required": ["from", "status", "description"],
                                         "properties": {
        "from": {"type": "array", "items": {"type": "string"}},
        "status": {"type": "string", "enum": [KEPT, RENAMED, MERGED, SEPARATED, REPEATED]},
        "description": {"type": "string"}, "reason": {"type": "string"}, "question": {"type": "string"},
        "sub_categories": {"type": "array", "items": {"type": "string"}},
        "eligibility": {"type": "array", "items": {"type": "string"}},
        "applies_to": {"type": "array", "items": {"type": "string", "enum": BUSINESS_TYPES}}}}},
    "additional": {"type": "array", "items": {"type": "object", "required": ["description", "reason"],
                                              "properties": {"description": {"type": "string"},
                                                             "group": {"type": "string"},
                                                             "reason": {"type": "string"}}}}}}


def build_prompt(drafts: List[Draft], past: List[str], target: Optional[Tuple[int, int]]) -> str:
    dups = duplicates(drafts)
    lines = [f"DRAFT ({len(drafts)} rows; id | group | description):"]
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
                target: Optional[Tuple[int, int]]) -> Optional[dict]:
        try:
            return self.llm.complete_json(SYSTEM, build_prompt(drafts, past, target), SCHEMA,
                                          "category_refinement", client=client, max_tokens=32000)
        except LLMError as e:
            print(f"refine: no model proposal ({e}); proposing by rules")
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
            print(f"refine: comments not interpreted ({e})")
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
    status, reason = raw["status"], (raw.get("reason") or "").strip()
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
                                  a["reason"].strip()))
    for ln in lines:
        new = title_case(ln.description)
        if new != ln.description and ln.status == KEPT:
            ln.status, ln.reason = RENAMED, f"capitalisation; was: {ln.description}"
        ln.description = new
        ln.applies_to = [b for b in BUSINESS_TYPES if b in (ln.applies_to or rule_applies_to(ln.description))]
        _settle_eligibility(ln, by_id)
    order = {d.id: i for i, d in enumerate(drafts)}
    gpos = {g: i for i, g in enumerate(groups_order)}
    lines.sort(key=lambda ln: (gpos.get(ln.group, len(gpos)),
                               min((order[i] for i in ln.from_draft), default=len(order))))
    return lines


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
                   "Question for client", "Sub-categories", "Eligibility", "Suggested eligibility (not applied)",
                   "Gate Question", "Applies To", "Decision", "Client comment", "Group"]
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
                  target: Optional[Tuple[int, int]] = None):
    problems = traceability(lines, drafts)
    if problems:
        raise ValueError("revised list not written: " + "; ".join(problems))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Draft"
    ws.append(["Draft #", "Group", "Category Description"])
    for d in drafts:
        ws.append([d.id, d.group, d.description])
    for c, w in zip(ws[1], [9, 34, 100]):
        c.font, c.fill = Font(bold=True), HEAD
        ws.column_dimensions[c.column_letter].width = w

    ws = wb.create_sheet("Revised")
    _write_lines(ws, lines, REVISED_HEADERS, lambda n, ln: [
        n, ln.code, ln.description, ln.status, ", ".join(ln.from_draft), ln.reason, ln.question,
        " | ".join(ln.sub_categories), " | ".join(ln.eligibility), " | ".join(ln.suggested_eligibility),
        "TRUE" if ln.gate_question else "", ", ".join(ln.applies_to), ln.decision, ln.comment, ln.group or ""],
        [5, 13, 60, 20, 10, 40, 40, 36, 28, 34, 9, 34, 11, 30, 4])
    decision, comment = REVISED_HEADERS.index("Decision") + 1, REVISED_HEADERS.index("Client comment") + 1
    for row in ws.iter_rows(min_row=2):
        if row[3].value:                                   # a line, not a group row
            row[decision - 1].fill = row[comment - 1].fill = ANSWER
    ws.column_dimensions["O"].hidden = True

    ws = wb.create_sheet("Summary")
    kept = [ln for ln in lines if ln.status != REPEATED]
    rows = [["Client", client], ["Draft rows", len(drafts)], ["Proposed categories", len(kept)]]
    if target:
        rows.append(["Target", f"{target[0]}-{target[1]}"])
    rows += [[], ["Status", "Lines"]] + [[s, n] for s, n in Counter(ln.status for ln in lines).most_common()]
    rows += [[], ["Group", "Draft rows", "Proposed"]]
    for g in groups_of(drafts) + ([None] if any(ln.group is None for ln in lines) else []):
        rows.append([g or "OTHER", sum(d.group == g for d in drafts), sum(ln.group == g for ln in kept)])
    rows += [[], ["How to answer: the Decision column of the Revised tab"],
             ["blank / accept", "as proposed"], ["reject", "back to the draft row(s)"],
             ["remove", "the category is dropped"], ["edit", "use the wording typed in Client comment"],
             ["", "A Client comment without a decision is a note: it is turned into a proposed change for "
                  "the team, never applied on its own. Other cells (Eligibility, Applies To...) may be "
                  "edited directly."],
             ["Eligibility", "Only what the draft states is applied: every document there becomes a mandatory "
                             "pass/fail upload in the category's sheet. 'Suggested eligibility' lists documents "
                             "proposed beyond the draft; copy any the client wants into Eligibility."]]
    for r in rows:
        ws.append(r)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 60
    wb.save(path)


def _split(text, sep: str) -> List[str]:
    return [x.strip() for x in str(text or "").split(sep) if x.strip()]


def read_revised(path: str) -> Tuple[List[Draft], List[Line]]:
    wb = openpyxl.load_workbook(path)
    drafts = [Draft(str(r[0]), str(r[2] or ""), r[1] or None)
              for r in wb["Draft"].iter_rows(min_row=2, values_only=True) if r[0] is not None]
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
                          _split(g("Suggested eligibility (not applied)"), "|")
                          if "Suggested eligibility (not applied)" in col else []))
    return drafts, lines


# --- step B: apply the review ---------------------------------------------------

def _original(d: Draft) -> Line:
    return Line(title_case(d.description), KEPT, [d.id], d.group, "decision: reject (back to the draft row)",
                applies_to=rule_applies_to(d.description), eligibility=rule_eligibility(d.description))


def apply_review(drafts: List[Draft], lines: List[Line]) -> Tuple[List[Line], List[Line], List[str]]:
    """Returns (final lines, excluded lines, problems)."""
    by_id = {d.id: d for d in drafts}
    final, excluded, problems, undone = [], [], [], set()
    undone_separations = {ln.from_draft[0] for ln in lines if ln.status == SEPARATED and ln.decision == "reject"}
    for ln in lines:
        d = ln.decision
        if d not in ("", "accept", "reject", "remove", "edit"):
            problems.append(f"'{ln.description}': decision '{ln.decision}' is not accept, reject, remove or edit; "
                            f"taken as accept")
            d = ""
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
                 "Gate Question", "Applies To", "From draft #", "Group"]


def write_final(path: str, final: List[Line], notes: List[Tuple[Line, str, str]]):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Final Categories"
    _write_lines(ws, final, FINAL_HEADERS, lambda n, ln: [
        n, ln.code, ln.description, " | ".join(ln.sub_categories), " | ".join(ln.eligibility),
        "TRUE" if ln.gate_question else "", ", ".join(ln.applies_to), ", ".join(ln.from_draft), ln.group or ""],
        [5, 13, 70, 36, 28, 9, 34, 10, 4])
    ws.column_dimensions["I"].hidden = True
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
                     ln.gate_question, ln.applies_to) for i, ln in enumerate(final, 1)]


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
