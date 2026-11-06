"""The category list, shaped by the team's comments (spec 21).

The list starts as the client's draft exactly as written. A team member
writes a comment, as they write feedback ("use PSI's groups", "keep the
client's wording", "put these under Project Based Consultancy"); the model
reads it as instructions and changes; code checks the answer before anyone
sees it, and the person applies the ticked changes or rejects them.

The model only proposes. Code:
- refuses changes that name rows the list does not have;
- does not trust "done": each instruction names the lines it means to
  change, and a missing one is asked for again, once, before the
  instruction is shown as partly done or not understood;
- flags few, real risks (wording found in no source, a change of what a
  category covers, a merge undone), and flagged changes start unticked.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from . import brief as cb
from .categories import Category
from .llm import LLMError, make_llm
from .schema import BUSINESS_TYPES, INDIVIDUAL

log = logging.getLogger("tendersure")


# --- the list -------------------------------------------------------------------------------

@dataclass
class Row:
    key: str
    group: str
    description: str
    subs: List[str] = field(default_factory=list)
    from_draft: List[str] = field(default_factory=list)


_OLD_NUMBER = re.compile(r"^\d{1,3}[.)]?\s+(?=[A-Za-z(])")


def from_drafts(drafts) -> List[Row]:
    """The list as the client wrote it: no rewording, no title case (spec 21 A). Only a list number left at the
    start of a numbered row's text ("3 Provision of ...", spec 18 A) is dropped: it is not wording."""
    def text(d) -> str:
        own = not d.id.rsplit("-", 1)[-1].startswith("r")
        return _OLD_NUMBER.sub("", d.description) if own else d.description
    return [Row(d.id, (d.group or "").strip(), text(d), list(d.sub_categories), [d.id]) for d in drafts]


@dataclass
class Reference:
    """One of the team's approved lists for another client: a source a comment may name."""
    client: str
    rows: List[Tuple[str, str, List[str]]]          # (group, description, sub-categories)


KINDS = ["reword", "group", "remove", "fold", "subs", "add", "split", "order", "merge"]


@dataclass
class Change:
    kind: str
    id: str = ""                                    # set by code, for ticking
    key: str = ""
    keys: List[str] = field(default_factory=list)   # merge: the rows that become one (the first keeps its place)
    target: str = ""                                # fold: the row it goes under
    text: str = ""                                  # reword / add / merge: the wording
    group: str = ""                                 # group / add / merge
    subs: List[str] = field(default_factory=list)   # subs / add
    texts: List[str] = field(default_factory=list)  # split: the new categories, in order
    groups: List[str] = field(default_factory=list)  # order: the group headings in order
    why: str = ""
    instruction: int = -1                           # which instruction of the comment it carries out
    source: str = ""                                # where the wording came from
    source_text: str = ""                           # the exact line copied from that source
    risky: str = ""                                 # set by code: why to look before applying ("" = no reason)

    def rows_named(self) -> List[str]:
        return [k for k in [self.key, self.target, *self.keys] if k]


def _new_key(rows: List[Row]) -> str:
    n = max((int(r.key[1:]) for r in rows if re.fullmatch(r"N\d+", r.key)), default=0)
    return f"N{n + 1}"


def apply(rows: List[Row], changes: Sequence[Change]) -> List[Row]:
    """The list after the changes, in order. A change naming a row that is gone is skipped."""
    out = [Row(r.key, r.group, r.description, list(r.subs), list(r.from_draft)) for r in rows]
    find = lambda k: next((r for r in out if r.key == k), None)
    for c in changes:
        if c.kind == "add":
            at = max((i for i, r in enumerate(out) if r.group == c.group), default=len(out) - 1)
            out.insert(at + 1, Row(_new_key(out), c.group, c.text, list(c.subs), []))
            continue
        if c.kind == "order":
            pos = {g.lower(): i for i, g in enumerate(c.groups)}
            out = [r for _, r in sorted(enumerate(out), key=lambda t: (pos.get(t[1].group.lower(), len(pos)), t[0]))]
            continue
        if c.kind == "merge":
            parts = [p for p in (find(k) for k in c.keys) if p]
            if len(parts) < 2:
                continue
            keep, rest = parts[0], parts[1:]
            keep.description, keep.group = c.text or keep.description, c.group or keep.group
            for r in rest:
                keep.from_draft += [d for d in r.from_draft if d not in keep.from_draft]
                keep.subs += [s for s in r.subs if s not in keep.subs]
                out.remove(r)
            continue
        row = find(c.key)
        if row is None:
            continue
        if c.kind == "reword":
            row.description = c.text
        elif c.kind == "group":
            row.group = c.group
        elif c.kind == "subs":
            row.subs = list(c.subs)
        elif c.kind == "remove":
            out.remove(row)
        elif c.kind == "split":
            i = out.index(row)
            out[i:i + 1] = [Row(f"{row.key}.{j + 1}", row.group, t, list(row.subs) if j == 0 else [],
                                list(row.from_draft)) for j, t in enumerate(c.texts)]
        elif c.kind == "fold":
            target = find(c.target)
            if target and target is not row:
                target.subs += [s for s in [row.description, *row.subs] if s not in target.subs]
                target.from_draft += [d for d in row.from_draft if d not in target.from_draft]
                out.remove(row)
    return out


# --- reading a comment ------------------------------------------------------------------------

@dataclass
class Instruction:
    text: str
    status: str                                     # done, nothing_to_change, partly_done, unclear
    note: str = ""
    targets: List[str] = field(default_factory=list)  # the rows it means to change


@dataclass
class Reading:
    changes: List[Change]
    instructions: List[Instruction]
    note: str
    raw: Dict = field(default_factory=dict)         # the model's answers, as they came (for the log)


SYSTEM = """You apply one comment from a procurement team member to a supplier-prequalification category list.

A comment may hold several instructions. List them under "instructions", one each, in the comment's order, with:
- status: done (you made changes for it), nothing_to_change (the list already does what it asks), or unclear (you
  need more; say what in its note);
- targets: the keys of every CURRENT LIST row the instruction changes (none for a new category or an order).
Every change gives the index (from 0) of the instruction it carries out. An instruction marked done must have its
changes in "changes": describing a change in a note does not make it. When an instruction names several
categories, give a change for each of them.

Make exactly the changes the comment asks for, and nothing else. Do not improve, reword or regroup anything the
comment does not ask about.

When an instruction says to take wording, groups or sub-categories from a source (the client's draft, or one of the
team's approved lists for another client), copy them exactly from that source, character for character, and give
the source's name in "source" and the exact line you copied in "source_text". Only treat two categories as "the
same" when they cover the same goods or services; a category that merely sounds alike is not the same. Never undo
a merge (a row made from several draft rows) unless an instruction asks for it.

Each change names rows by their key from CURRENT LIST:
- reword: key, text (the new wording)
- group: key, group (move the row under that group heading)
- remove: key
- fold: key, target (the row becomes a sub-category of the target row; its sub-categories move too)
- subs: key, subs (the row's full new list of sub-categories)
- add: text, group, subs (a new category)
- split: key, texts (the row is replaced by these categories, in this order)
- order: groups (the group headings in the order they should appear)
- merge: keys (the rows that become one category, two or more), text (its wording: always give it), group
Give each change a short "why" that points to the comment or the source used."""

SCHEMA = {"type": "object", "required": ["instructions", "changes", "note"], "properties": {
    "note": {"type": "string"},
    "instructions": {"type": "array", "items": {"type": "object", "required": ["text", "status"], "properties": {
        "text": {"type": "string"}, "status": {"type": "string", "enum": ["done", "nothing_to_change", "unclear"]},
        "note": {"type": "string"}, "targets": {"type": "array", "items": {"type": "string"}}}}},
    "changes": {"type": "array", "items": {"type": "object", "required": ["kind", "instruction"], "properties": {
        "kind": {"type": "string", "enum": KINDS}, "instruction": {"type": "integer"},
        "key": {"type": "string"}, "target": {"type": "string"}, "text": {"type": "string"},
        "group": {"type": "string"}, "why": {"type": "string"},
        "keys": {"type": "array", "items": {"type": "string"}},
        "source": {"type": "string"}, "source_text": {"type": "string"},
        "subs": {"type": "array", "items": {"type": "string"}},
        "texts": {"type": "array", "items": {"type": "string"}},
        "groups": {"type": "array", "items": {"type": "string"}}}}}}}


def build_prompt(comment: str, rows: List[Row], drafts, references: List[Reference], brief: str = "") -> str:
    out = cb.prompt_lines(brief) + [f"COMMENT: {comment}", "",
                                    "CURRENT LIST (key | group | description | sub-categories | from draft rows):"]
    out += [f"  {r.key} | {r.group} | {r.description} | {' ; '.join(r.subs)} | {', '.join(r.from_draft)}" for r in rows]
    out += ["", "THE CLIENT'S DRAFT, AS WRITTEN (draft row | group | description | sub-categories):"]
    out += [f"  {d.id} | {d.group or ''} | {d.description} | {' ; '.join(d.sub_categories)}" for d in drafts]
    for ref in references:
        out += ["", f"THE TEAM'S APPROVED LIST FOR {ref.client} (group | description | sub-categories):"]
        out += [f"  {g or ''} | {d} | {' ; '.join(s)}" for g, d, s in ref.rows]
    return "\n".join(out)


# --- checks -----------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


_SMALL = {"of", "and", "the", "for", "to", "in", "etc", "e", "g", "supply", "provision", "services", "service",
          "delivery", "installation", "maintenance", "related", "items", "a", "or", "with", "including", "i"}


def _stem(w: str) -> str:
    """Plurals and doubled letters aside: stationeries / stationery, tonners / toners."""
    w = re.sub(r"ies$", "y", w)
    w = re.sub(r"(es|s)$", "", w) if len(w) > 4 else w
    return re.sub(r"(.)\1", r"\1", w)


def _words(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _SMALL}


def overlap(a: str, b: str) -> float:
    """Share of the shorter wording's words found in the other (small and procurement words aside)."""
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / min(len(wa), len(wb)) if wa and wb else 0.0


def _asked_for(text: str, comment: str) -> bool:
    """The comment names this wording: it contains it, or quotes a name (three words or more) it starts with."""
    t = _norm(text)
    if t and t in _norm(comment):
        return True
    quoted = [_norm(q) for q in re.findall(r"[\"“”']([^\"“”']{8,})[\"“”']", comment)]
    return any(len(q.split()) >= 3 and t.startswith(q) for q in quoted)


def risk(ch: Change, rows: Dict[str, Row], drafts, refs: Sequence[Reference], comment: str = "") -> str:
    """Why a person should look at a change before applying it (spec 21 C); "" when there is no reason.
    Only real risks: a flag on every change teaches people to ignore flags, or to leave good changes out."""
    if ch.kind in ("reword", "add", "merge") and ch.text and _asked_for(ch.text, comment):
        return ""
    reasons = []
    own = {d.id: d.description for d in drafts}
    if ch.kind in ("reword", "add") and ch.source and ch.source.lower() != "comment":
        pool = list(own.values()) + [d for r in refs for _, d, _ in r.rows]
        if not any(_norm(p) == _norm(ch.text) for p in pool):
            reasons.append("its wording is not in the draft or any approved list, word for word")
    if ch.kind == "reword":
        row = rows[ch.key]
        back_to_own = any(_norm(own.get(i, "")) == _norm(ch.text) for i in row.from_draft)
        if len(row.from_draft) > 1 and back_to_own:
            reasons.append(f"undoes a merge: this line joins {len(row.from_draft)} draft rows, "
                           f"the new wording is one of them")
        elif not back_to_own and overlap(row.description, ch.text) < 2 / 3:
            reasons.append("changes what the category covers")
    return "; ".join(reasons)


def check(raw: dict, rows: List[Row], drafts=(), refs: Sequence[Reference] = (),
          comment: str = "") -> Tuple[List[Change], List[str]]:
    """The model's changes that name real rows, each with its reasons to look before applying;
    the others are reported, never shown as changes."""
    by_key = {r.key: r for r in rows}
    ok, problems = [], []
    for c in raw.get("changes", []):
        ch = Change(**{k: v for k, v in c.items() if k in Change.__dataclass_fields__ and k not in ("id", "risky")})
        keys_ok = len(set(ch.keys)) >= 2 and all(k in by_key for k in ch.keys)
        if ch.kind not in KINDS:
            problems.append(f"unknown change '{ch.kind}'")
        elif ch.kind in ("reword", "group", "remove", "fold", "subs", "split") and ch.key not in by_key:
            problems.append(f"{ch.kind}: no row '{ch.key}'")
        elif ch.kind == "fold" and (ch.target not in by_key or ch.target == ch.key):
            problems.append(f"fold {ch.key}: no row '{ch.target}' to put it under")
        elif ch.kind == "merge" and not keys_ok:
            problems.append(f"merge {', '.join(ch.keys)}: needs two or more rows of the list")
        elif ch.kind in ("reword", "add") and not ch.text.strip():
            problems.append(f"{ch.kind}: no wording given")
        elif ch.kind == "split" and len([t for t in ch.texts if t.strip()]) < 2:
            problems.append(f"split {ch.key}: fewer than two pieces")
        elif ch.kind == "group" and not ch.group.strip():
            problems.append(f"group {ch.key}: no group given")
        else:
            if ch.kind == "merge" and not ch.text.strip():
                # the model often leaves a merge's wording out: it keeps the first row's (said, not flagged)
                ch.text = by_key[ch.keys[0]].description
                ch.why = f"{ch.why} (no wording came with it: it keeps the first row's)".strip()
            ch.texts = [t for t in ch.texts if t.strip()]
            ch.risky = risk(ch, by_key, drafts, refs, comment)
            ok.append(ch)
    return ok, problems


_TARGET_LEAD = re.compile(r"(\binto|\bto|\bas|\busing|category:?|categories:?)\s+(\w+'s\s+)?(one category:?\s+)?$", re.I)


def _quoted_name(text: str, sources: Sequence[str] = ()) -> str:
    """The name an instruction gives the result: a quoted name (three words or more) right after "into", "to",
    "as", "using" or "category:" - not a name it merges in ("Merge 'X' into software solutions"). When the
    quote is the start of exactly one line of the sources, that whole line ("using PSI's 'Supply and Rental of
    Audio-Visual Equipment' wording" -> PSI's full line). "" when the instruction names no result."""
    for m in re.finditer(r"[\"“”']([^\"“”']{8,})[\"“”']", text or ""):
        q = m.group(1).strip()
        if len(q.split()) < 3 or not _TARGET_LEAD.search(text[max(0, m.start() - 40):m.start()]):
            continue
        whole = {_norm(x): x for x in sources if _norm(x).startswith(_norm(q))}
        return next(iter(whole.values())) if len(whole) == 1 else q
    return ""


def _conflicts(changes: List[Change]):
    """Two changes of one comment that set the same line's wording: the later one is flagged (and so starts
    unticked), so it cannot quietly undo the earlier (Amref: "use PSI's wording" then "change the brackets"
    rewrote Relief NFI from the client's text)."""
    first: Dict[str, Change] = {}
    for c in changes:
        key = c.keys[0] if c.kind == "merge" and c.keys else c.key
        if c.kind not in ("reword", "merge", "split") or not key:
            continue
        if key in first:
            earlier = first[key]
            note = (f"another change in this comment also rewords this line (to “{(earlier.text or '')[:60]}”); "
                    f"tick one of them")
            c.risky = f"{c.risky}; {note}" if c.risky else note
        else:
            first[key] = c


def _touched(changes: List[Change], i: int) -> set:
    return {k for c in changes if c.instruction == i for k in c.rows_named()}


def read(comment: str, rows: List[Row], drafts, references: Sequence[Reference], client: str, brief: str = "",
         model: Optional[str] = None, llm=None) -> Reading:
    """What the comment asks for: its instructions and the checked changes for each (spec 21 B)."""
    llm = llm or make_llm(model=model)
    refs = list(references)

    def ask(text: str) -> dict:
        return llm.complete_json(SYSTEM, build_prompt(text, rows, drafts, refs, brief), SCHEMA, "category_comment",
                                 client=client, max_tokens=32000)
    try:
        raw = ask(comment)
    except LLMError as e:
        return Reading([], [], f"The AI did not answer: {e}")
    answers = {"first": raw}
    changes, problems = check(raw, rows, drafts, refs, comment)
    keys = {r.key for r in rows}
    instructions = [Instruction(i.get("text", ""), i.get("status", "unclear"), i.get("note", ""),
                                [t for t in i.get("targets", []) or [] if t in keys])
                    for i in raw.get("instructions", [])]

    # "done" is not trusted: an instruction with no change, or with targets left unchanged, is asked again, once
    def left(i: int) -> List[str]:
        return [t for t in instructions[i].targets if t not in _touched(changes, i)]
    again = [i for i, ins in enumerate(instructions)
             if ins.status == "done" and (not any(c.instruction == i for c in changes) or left(i))]
    if again:
        # One at a time: a long answer is where the model stops early (Amref: 13 instructions read, one change
        # returned; asked again for the other 12 together, one change again). Single instructions work.
        def one(i: int):
            text = instructions[i].text
            if left(i):
                text += f" (Rows still to change: {', '.join(left(i))}.)"
            try:
                return i, ask(text), None
            except LLMError as e:
                return i, None, str(e)
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(one, again))
        answers["again"] = {}
        for i, raw2, error in replies:
            if error:
                problems.append(f"asking again for instruction {i + 1} failed: {error}")
                continue
            answers["again"][str(i)] = raw2
            more, more_problems = check(raw2, rows, drafts, refs, comment)
            for c in more:
                c.instruction = i                   # the answer was about this one instruction
            changes += more
            problems += more_problems
    by_key = {r.key: r for r in rows}
    for i in again:
        if not any(c.instruction == i for c in changes):
            instructions[i].status = "unclear"
            instructions[i].note = ("The AI said this was done, but no change came back, even when asked for this "
                                    "instruction alone. Try it again, naming the categories.")
        elif left(i):
            instructions[i].status = "partly_done"
            names = "; ".join(by_key[k].description[:60] for k in left(i))
            instructions[i].note = f"No change came for: {names}. Ask for these again by name."
    sources = [d.description for d in drafts] + [d for r in refs for _, d, _ in r.rows]
    for c in changes:                               # a wordless merge takes the name its instruction gives the result
        if c.kind == "merge" and "keeps the first row's" in c.why and 0 <= c.instruction < len(instructions):
            name = _quoted_name(instructions[c.instruction].text, sources)
            if name:
                c.text = name
                c.why = c.why.replace("(no wording came with it: it keeps the first row's)", "(wording from the comment)")
    _conflicts(changes)
    for n, c in enumerate(changes, 1):
        c.id = f"c{n}"
    note = " ".join(x for x in [raw.get("note", "").strip()] + problems if x)
    return Reading(changes, instructions, note, answers)


# --- closeness to a list the team made -------------------------------------------------------

SAME, CLOSE = 0.92, 0.6


def _score(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def compare(rows: List[Row], target: List[Tuple[str, str, List[str]]]) -> Dict:
    """How close the list is to a target list of (group, description, sub-categories): each target
    category matched to at most one row, best pairs first (spec 21 F)."""
    pairs = sorted(((_score(t[1], r.description), i, j) for i, t in enumerate(target) for j, r in enumerate(rows)),
                   reverse=True)
    used_t, used_r, matched = set(), set(), []
    for s, i, j in pairs:
        if s < CLOSE:
            break
        if i in used_t or j in used_r:
            continue
        used_t.add(i)
        used_r.add(j)
        matched.append((s, i, j))
    close = [m for m in matched if m[0] < SAME]
    return {
        "target": len(target), "ours": len(rows),
        "same_wording": sum(m[0] >= SAME for m in matched), "close_wording": len(close),
        "same_group": sum(_norm(target[i][0]) == _norm(rows[j].group) for _, i, j in matched),
        "missing": [target[i][1] for i in range(len(target)) if i not in used_t],
        "extra": [r.description for j, r in enumerate(rows) if j not in used_r],
        "close_pairs": [{"target": target[i][1], "ours": rows[j].description, "score": round(s, 2)}
                        for s, i, j in sorted(close)],
        "group_differs": [{"category": target[i][1], "target": target[i][0], "ours": rows[j].group}
                          for _, i, j in matched if _norm(target[i][0]) != _norm(rows[j].group)],
    }


# --- outputs ----------------------------------------------------------------------------------

def to_categories(rows: List[Row], drafts, client: str, version: Optional[str] = None) -> List[Category]:
    """The categories table for criteria (spec 21 G): one category per line, with what its draft rows state
    (licence, HR, technical, Individual Consultant); Gate Question and Applies To by the rules, as today."""
    from .refine import COMPANIES, rule_applies_to, rule_eligibility
    by_id = {d.id: d for d in drafts}
    marked = any(d.individual for d in drafts)
    out = []
    for n, r in enumerate(rows, 1):
        src = [by_id[i] for i in r.from_draft if i in by_id]
        union = lambda name: list(dict.fromkeys(x for d in src for x in getattr(d, name)))
        if marked:
            applies = BUSINESS_TYPES[:] if any(d.individual for d in src) else COMPANIES[:]
        else:
            applies = rule_applies_to(r.description)
        # only what the client's own wording states (spec 19 A): a line's wording may now come from elsewhere
        eligibility = list(dict.fromkeys(e for d in src for e in rule_eligibility(d.description)))
        out.append(Category(n, r.description, r.group or None, list(r.subs), client, version, "", eligibility,
                            INDIVIDUAL in applies, applies, union("licence"), union("hr"), union("technical")))
    return out


def write_client_workbook(path: str, rows: List[Row], title: str = "Categories"):
    """The list in the client's layout: group rows, #, Category Description, sub-categories one per row."""
    from .refine import KEPT, Line, write_client_list
    write_client_list(path, [Line(r.description, KEPT, list(r.from_draft), r.group or None,
                                  sub_categories=list(r.subs)) for r in rows], title)
