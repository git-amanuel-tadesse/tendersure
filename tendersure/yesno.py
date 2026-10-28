"""Yes/No questions (spec 02 G10, feedback item 11).

Whether Yes is the good answer decides the answer type: Yes good, or no
marks -> type 4 (a Yes/No toggle); Yes bad with marks -> type 2, options
Yes,No scored 0,<marks>. The rule overrides the client's past sheets: ERCS
scores Yes 0 on "Does your company recycle ... waste responsibly", so a
supplier who recycles loses the marks. Past scoring is therefore not evidence
of which answer is good; the question's meaning is.

The verdict is kept once per question in data/yesno_polarity.csv. Questions
not there are judged in one model call per batch (anonymised like every
call), else by a keyword rule; both are drafts the team confirms there.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from . import grouping
from .generate import Plan, PlannedRow
from .learned_store import YES_NO, resolve
from .llm import LLMError, make_llm
from .trust import context_key
from .validate import YES_BAD, YES_GOOD, is_yes_no, yes_no_target

DRAFTED, RULE_DRAFTED, CONFIRMED = "drafted", "drafted (rules)", "confirmed"
SIMILAR = "drafted (from similar)"   # taken from a confirmed rewording of the question
CHUNK = 40                           # questions per model call (spec 04 L5)
COLUMNS = ["question_key", "example", "yes_is", "status"]
_RISK = re.compile(r"litigation|criminal|default|terminat|negative media|adverse|debar|sanction|blacklist|"
                   r"bankrupt|insolven|conflict of interest|associated with|politically exposed|fraud|"
                   r"corrupt|penalt|convict|suspend|warrant|summons|dispute|legal (?:action|process)|"
                   r"lawsuit|fined|violation|breach", re.I)


@dataclass
class Verdict:
    question_key: str
    example: str
    yes_is: str
    status: str = DRAFTED


class Book:
    """Yes is good or bad, per question."""

    def __init__(self, verdicts: List[Verdict]):
        self.verdicts = verdicts
        self._by = {v.question_key: v for v in verdicts}

    def lookup(self, key: str) -> Optional[Verdict]:
        return self._by.get(key)

    def add(self, v: Verdict):
        old = self._by.get(v.question_key)
        if old is not None:
            self.verdicts.remove(old)
        self.verdicts.append(v)
        self._by[v.question_key] = v


def load(target) -> Book:
    """From a storage driver (see learned_store) or a CSV path."""
    store, name = resolve(target, YES_NO)
    return Book([Verdict(d["question_key"], d["example"], d["yes_is"], d["status"] or DRAFTED)
                 for d in store.read(name)])


def save(book: Book, target):
    store, name = resolve(target, YES_NO)
    store.write(name, COLUMNS, [{"question_key": v.question_key, "example": v.example, "yes_is": v.yes_is,
                                 "status": v.status}
                                for v in sorted(book.verdicts, key=lambda v: (v.yes_is, v.question_key))])


def rule_yes_is(text: str) -> str:
    """Yes is bad when the question asks about a risk (litigation, default,
    termination, sanctions, conflict of interest...); good otherwise."""
    return YES_BAD if _RISK.search(text) else YES_GOOD


@dataclass
class Need:
    client: str
    key: str
    example: str


class RuleJudge:
    redoes_rules = False

    def judge(self, needs: List[Need]) -> List[Verdict]:
        return [Verdict(n.key, n.example, rule_yes_is(n.example), RULE_DRAFTED) for n in needs]


SYSTEM = (
    "A buyer asks suppliers these Yes/No questions in a supplier prequalification questionnaire. "
    "For each, say whether answering Yes is good for the buyer (a capability, policy, service or "
    "commitment they want: 'good') or bad (a risk such as litigation, default, termination, "
    "sanctions or a conflict of interest: 'bad')."
)
SCHEMA = {"type": "object", "required": ["answers"], "properties": {"answers": {"type": "array", "items": {
    "type": "object", "required": ["id", "yes_is"],
    "properties": {"id": {"type": "integer"}, "yes_is": {"type": "string", "enum": [YES_GOOD, YES_BAD]}}}}}}


class ModelJudge:
    """Verdicts from any configured model; the keyword rule for anything it
    misses. Questions go CHUNK at a time; a failed call costs only its chunk."""
    redoes_rules = True
    chunk = CHUNK

    def __init__(self, llm=None, model: Optional[str] = None):
        self.llm = llm or make_llm(model=model)

    def _ask(self, client: str, group: List[Need]) -> Dict[int, str]:
        prompt = "QUESTIONS:\n" + "\n".join(f"  {i} | {n.example}" for i, n in enumerate(group))
        try:
            raw = self.llm.complete_json(SYSTEM, prompt, SCHEMA, "yes_no_polarity", client=client, max_tokens=4000)
            return {a["id"]: a["yes_is"] for a in raw["answers"]}
        except LLMError as e:
            print(f"yes/no: no model verdicts for {client} ({e}); judged by keywords")
            return {}

    def judge(self, needs: List[Need]) -> List[Verdict]:
        by_client: Dict[str, List[Need]] = defaultdict(list)
        for n in needs:
            by_client[n.client].append(n)
        out = []
        for client, group in by_client.items():
            for part in (group[i:i + self.chunk] for i in range(0, len(group), self.chunk)):
                answers = self._ask(client, part)
                out += [Verdict(n.key, n.example, answers[i], DRAFTED) if i in answers else
                        Verdict(n.key, n.example, rule_yes_is(n.example), RULE_DRAFTED)
                        for i, n in enumerate(part)]
        return out


def _yes_no(rows: List) -> List[Tuple[int, str]]:
    return [(i, context_key(rows, i)) for i, r in enumerate(rows) if is_yes_no(r)]


def _scored_yes_no(plan: Plan) -> List[Tuple[PlannedRow, str]]:
    qs = plan.questions
    return [(qs[i], key) for i, key in _yes_no([p.row for p in qs])]


def needs_in(sheets: Iterable[Tuple[str, List]], book: Book, redo_rules: bool) -> List[Need]:
    """Scored Yes/No questions with no verdict yet, once each. `sheets` are
    (client, rows) pairs: generated plans or past sheets."""
    out: Dict[str, Need] = {}
    for client, rows in sheets:
        for i, key in _yes_no(rows):
            v = book.lookup(key)
            if (rows[i].marks or 0) > 0 and (v is None or (redo_rules and v.status == RULE_DRAFTED)):
                out.setdefault(key, Need(client, key, rows[i].criteria))
    return list(out.values())


def needs(plans: List[Plan], book: Book, redo_rules: bool) -> List[Need]:
    return needs_in(((p.target.client, [q.row for q in p.questions]) for p in plans), book, redo_rules)


def apply(plan: Plan, book: Book):
    for p, key in _scored_yes_no(plan):
        r, v = p.row, book.lookup(key)
        marks = r.marks or 0
        target = yes_no_target(v.yes_is if v else None, marks)
        if target is None:
            p.note("scored Yes/No question with no verdict on whether Yes is good; left as copied", adapted=False)
            continue
        if (r.answer_type, r.options, r.option_scores) != target:
            why = "no marks" if marks <= 0 else f"Yes is {v.yes_is}"
            p.note(f"Yes/No: {why} -> answer type {target[0]}"
                   + (f", options Yes,No scored {target[2]}" if target[1] else ""))
            r.answer_type, r.options, r.option_scores = target


def reuse_similar(needs_: List[Need], book: Book) -> Tuple[List[Verdict], List[Need]]:
    """A question takes the confirmed verdict of a rewording of it (spec 04
    L2), as a draft: a rewording can flip the meaning ("... no contracts
    terminated"). Returns (reused verdicts, still needed)."""
    confirmed = {v.question_key: v for v in book.verdicts if v.status == CONFIRMED}
    if not confirmed:
        return [], needs_
    rep = grouping.group(list(dict.fromkeys(list(confirmed) + [n.key for n in needs_])),
                         apart=lambda a, b: a in confirmed and b in confirmed)
    by_group = {rep[k]: v for k, v in confirmed.items()}
    reused, rest = [], []
    for n in needs_:
        twin = by_group.get(rep[n.key])
        if twin:
            reused.append(Verdict(n.key, n.example, twin.yes_is, SIMILAR))
        else:
            rest.append(n)
    return reused, rest


def settle(plans: List[Plan], book: Book, judge) -> List[Verdict]:
    """Judge what the book lacks (adding it there), then apply the rule to
    every sheet. Returns the new verdicts."""
    reused, rest = reuse_similar(needs(plans, book, judge.redoes_rules), book)
    new = reused + judge.judge(rest)
    for v in new:
        book.add(v)
    for plan in plans:
        apply(plan, book)
    return new


def learn(sheets: Iterable[Tuple[str, List]], book: Book, judge) -> List[Verdict]:
    """A verdict for every scored Yes/No question in past sheets (spec 04 L4):
    reuse confirmed rewordings, judge the rest. Returns the new verdicts."""
    reused, rest = reuse_similar(needs_in(sheets, book, judge.redoes_rules), book)
    new = reused + judge.judge(rest)
    for v in new:
        book.add(v)
    return new
