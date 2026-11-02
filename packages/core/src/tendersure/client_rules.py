"""Client rules (spec 12 C): what a client wants changed on every sheet, or on
some groups' sheets, said once and applied on every run.

    remove   the questions whose wording contains `match`
    replace  the term `match` with `value` ("LPO" -> "PO", "business permit" -> "trade license")
    reword   a question containing `match` gets `value` as its whole wording
    limit    at most `value` questions in the section named `match`
    marks    the sub-section named `match` ("Ref 1") carries `value` marks; the
             other sections take up the difference (spec 14 C)
    add      the question `value` (ending "[n]" for its marks) is added at the end of
             the section named `match`, unless the sheet asks it already (spec 17 A)

`groups` scopes a rule by the words of a category's group: "engineering" for
those groups only, "!engineering" for every other; empty for all; "#12" names
category 12 (spec 17 A). Matching is
case-insensitive and on whole words. The Declaration and Submission are never
touched. A removed question's marks stay in its section (else in the sheet),
as for removals in the review.

The rules are client data, kept as the learned table `client_rules` behind
the storage driver, like the glossary.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from .learned_store import resolve
from .parser import section_key

RULES = "client_rules"
COLUMNS = ["client", "action", "match", "value", "groups", "note"]
ACTIONS = ("remove", "replace", "reword", "limit", "marks", "add")
NEVER = {"declaration", "submission"}


@dataclass
class Rule:
    client: str
    action: str
    match: str
    value: str = ""
    groups: str = ""
    note: str = ""

    def in_group(self, group: Optional[str], number: Optional[int] = None) -> bool:
        words = set(re.findall(r"[a-z]+", (group or "").lower()))
        terms = [t.strip().lower() for t in self.groups.split(",") if t.strip()]
        include = [t for t in terms if not t.startswith("!")]
        exclude = [t[1:].strip() for t in terms if t.startswith("!")]

        def hit(t: str) -> bool:
            if t.startswith("#"):                      # a category by number
                return number is not None and t[1:].strip().isdigit() and int(t[1:]) == number
            return set(re.findall(r"[a-z]+", t)) <= words
        return (not include or any(hit(t) for t in include)) and not any(hit(t) for t in exclude)

    def applies_to(self, plan) -> bool:
        """The rule's scope holds this plan's category (its group, or its number)."""
        return self.in_group(plan.target.group, number_of(plan.target.code))

    def wording(self) -> str:
        """An `add` rule's question, without its "[n]"."""
        return _ADD_MARKS.sub("", self.value).strip()

    def added_marks(self) -> float:
        m = _ADD_MARKS.search(self.value)
        return float(m.group(1)) if m else 0.0

    def pattern(self) -> re.Pattern:
        words = [re.escape(w) for w in self.match.split()]
        return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])", re.I)

    def problem(self) -> Optional[str]:
        """Why the rule cannot be applied, if it cannot."""
        if self.action not in ACTIONS:
            return f"action '{self.action}' is not one of {', '.join(ACTIONS)}"
        if not self.match.strip():
            return "no match given"
        if self.action in ("replace", "reword", "add") and not self.value.strip():
            return f"{self.action} needs a value"
        if self.action == "limit" and not self.value.strip().isdigit():
            return "limit needs a number as value"
        if self.action == "marks" and not re.fullmatch(r"\d+(\.\d+)?", self.value.strip()):
            return "marks needs a number as value"
        return None


_ADD_MARKS = re.compile(r"\s*\[\s*(\d+(?:\.\d+)?)\s*(?:marks?)?\s*\]\s*$", re.I)
_YES_NO = re.compile(r"^(please\s+confirm|confirm|do|does|did|are|is|was|were|has|have|can|will|would)\b|\byes\s*/\s*no\b", re.I)
_UPLOAD = re.compile(r"^(please\s+)?(attach|upload|submit)\b", re.I)


def number_of(code: Optional[str]) -> Optional[int]:
    m = re.search(r"(\d+)$", code or "")
    return int(m.group(1)) if m else None


def _norm(text) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def added_wordings(rules: List["Rule"], plan) -> set:
    """The questions the plan's `add` rules add, normalised: a `limit` never drops them."""
    return {_norm(r.wording()) for r in rules if r.action == "add" and r.applies_to(plan)}


def load(target) -> List[Rule]:
    store, name = resolve(target, RULES)
    return [Rule(**{c: (d.get(c) or "").strip() for c in COLUMNS}) for d in store.read(name)]


def save(rules: List[Rule], target):
    store, name = resolve(target, RULES)
    store.write(name, COLUMNS, [{c: getattr(r, c) for c in COLUMNS} for r in rules])


def for_client(rules: List[Rule], client: str) -> List[Rule]:
    return [r for r in rules if r.client.upper() == (client or "").upper() and not r.problem()]


def _keep_case(found: str, value: str) -> str:
    """The value as the team wrote it, capitalised where the replaced term began with a capital
    ("Business permit" -> "Trade license"; "LPO" -> "PO")."""
    titled = found[:1].isupper() and not (len(found) > 1 and found.isupper())     # "Business", not "PO"
    return value[:1].upper() + value[1:] if titled else value


def apply(plan, rules: List[Rule]):
    """The client's rules on one plan, in their order (spec 12 C3: before the template rules)."""
    from .generate import NEW, drop_empty_sections, drop_row, keep_section_totals, rebalance_others, rebalance_to_100
    mine = [r for r in rules if r.applies_to(plan) and r.action not in ("marks", "add")]
    mine += [r for r in rules if r.applies_to(plan) and r.action == "add"]        # added last (spec 17 A)
    if not mine:
        return
    added = added_wordings(rules, plan)
    before = {k: v[0] for k, v in plan.section_marks().items()}
    changed, grown = set(), set()
    for rule in mine:
        pat = rule.pattern()
        rows = [p for p in plan.questions if section_key(p.row.section) not in NEVER]
        why = f"client rule: {rule.note or rule.action + ' ' + rule.match}"
        if rule.action == "remove":
            for p in [p for p in rows if pat.search(p.row.criteria)]:
                changed.add(section_key(p.row.section))
                drop_row(plan, plan.rows.index(p), why)
        elif rule.action == "replace":
            for p in rows:
                new = pat.sub(lambda m: _keep_case(m.group(0), rule.value), p.row.criteria)
                if new != p.row.criteria:
                    p.note(f"{why}; was: {p.row.criteria}")
                    p.row.criteria = new
        elif rule.action == "reword":
            for p in rows:
                if pat.search(p.row.criteria) and p.row.criteria != rule.value:
                    p.note(f"{why}; was: {p.row.criteria}")
                    p.row.criteria = rule.value
        elif rule.action == "limit":
            key, n = section_key(rule.match), int(rule.value)
            section = [p for p in rows if section_key(p.row.section) == key and _norm(p.row.criteria) not in added]
            if len(section) <= n:
                continue
            # scored questions first, then the sheet's own, then those added by the model or research
            rank = sorted(section, key=lambda p: ((p.row.marks or 0) <= 0, p.origin == NEW, section.index(p)))
            keep = set(map(id, rank[:n]))
            for p in [p for p in section if id(p) not in keep]:
                changed.add(key)
                drop_row(plan, plan.rows.index(p), f"{why} (at most {n} {key} questions)")
        elif rule.action == "add":
            if _add(plan, rule, why):
                grown.add(section_key(rule.match))
    if changed:
        drop_empty_sections(plan)
        here = {section_key(p.row.section) for p in plan.questions}
        problems = keep_section_totals(plan, {k: before.get(k, 0) for k in changed if k in here}, "client rules")
        if problems or any(k not in here and before.get(k) for k in changed):
            extra = sum(p.row.marks or 0 for p in plan.questions if p.source == "client rule")
            rebalance_to_100(plan, "client rules", 100 + extra)      # the added rows' marks come after
    if grown:                                   # the added marks stay; the other sections give them up
        plan.fixed_sections |= grown
        if abs(plan.total() - 100) > 1e-6:
            rebalance_others(plan, grown, "the client's added questions")


def _add(plan, rule: Rule, why: str) -> bool:
    """One `add` rule on one plan; True when the row carries marks (its section grew)."""
    from .generate import ADAPTED, insert_rows
    from .parser import Row
    text, marks, key = rule.wording(), rule.added_marks(), section_key(rule.match)
    if any(_norm(p.row.criteria) == _norm(text) for p in plan.questions):
        return False
    if _UPLOAD.match(text):
        at_type, sys_m, comp_m = 5, None, marks
    elif _YES_NO.search(text):
        at_type, sys_m, comp_m = 4, marks, None
    else:
        at_type, sys_m, comp_m, marks = 1, None, None, 0.0          # a text answer cannot score
    r = Row(source_file="", sheet="", excel_row=0, section=SECTION_LABELS.get(key, rule.match.strip().title()),
            sub_section="N/A", criteria=text, marks=0.0, answer_type=at_type, mandatory=True, compliance_attach=at_type == 5)
    # at the end of the section; a sheet without it gets the section, in its place (insert_rows)
    insert_rows(plan, key, [r], ADAPTED, "client rule", why + (f" ({marks:g} marks)" if marks else ""),
                section_label=SECTION_LABELS.get(key))
    p = next(q for q in plan.questions if q.row is r)
    r.marks, r.system, r.compliance, p.weight_hint = marks, sys_m, comp_m, 0.0
    p.needs_reading = False
    return marks > 0


SECTION_LABELS = {"financial": "Financial Performance- FINP", "registration": "Registration and Statutory Requirements",
                  "technical": "Technical Specifications- TECH", "hr": "Human Resource- HRM",
                  "experience": "Business Experience- EXP", "esg": "Sustainable Procurement (ESG)- ESG",
                  "sla": "Service Level Requirements- SLA"}


def pending_add_marks(plan, rules: List[Rule]) -> dict:
    """{section: marks} the plan's `add` rules will bring when finalised (spec 17 A): an answer carried
    from the last run counts them already, so they come out of it before it is applied."""
    out = {}
    have = {_norm(p.row.criteria) for p in plan.questions}
    for r in rules:
        if r.action == "add" and r.applies_to(plan) and r.added_marks() and _norm(r.wording()) not in have:
            key = section_key(r.match)
            out[key] = out.get(key, 0) + r.added_marks()
    return out


def _name(text) -> str:
    return "".join(str(text or "").lower().split())


def apply_marks(plan, rules: List[Rule]):
    """The `marks` rules on one plan (spec 14 C), after every other template rule: each named
    sub-section carries its marks, and the sections no rule set take up the difference."""
    from .generate import rebalance_others, share_marks
    from .validate import asks_for_document
    mine = [r for r in rules if r.action == "marks" and r.applies_to(plan)]
    touched = set()
    for rule in mine:
        rows = [p for p in plan.questions if _name(p.row.sub_section) == _name(rule.match)
                and section_key(p.row.section) not in NEVER]
        if not rows:
            continue
        why = f"client rule: {rule.note or f'{rule.match} carries {rule.value} marks'}"
        scored = [p for p in rows if (p.row.marks or 0) > 0] or \
            [p for p in rows if asks_for_document(p.row)] or [p for p in rows if p.row.answer_type in (2, 3, 4, 5)]
        if not scored:
            plan.notes.append(f"{why}: '{rule.match}' has no question that can carry marks")
            continue
        for p in scored:
            p.weight_hint = p.weight_hint or 1.0
        share_marks(scored, float(rule.value), why)
        touched.add(section_key(rows[0].row.section))
    if touched:
        plan.fixed_sections |= touched
        rebalance_others(plan, set(), "the client's sub-section marks")


def fixed_sections(plan, rules: List[Rule]) -> dict:
    """{section: marks} the `marks` rules settle on this plan (spec 16 D): a section whose scored
    questions all sit in sub-sections the rules name carries the rules' total, whatever an
    answer says."""
    mine = {_name(r.match): float(r.value) for r in rules if r.action == "marks" and r.applies_to(plan)}
    out = {}
    for key in {section_key(p.row.section) for p in plan.questions}:
        rows = [p.row for p in plan.questions if section_key(p.row.section) == key]
        named = {_name(r.sub_section) for r in rows} & set(mine)
        if named and all(_name(r.sub_section) in named for r in rows if r.marks):
            out[key] = sum(mine[n] for n in named)
    return out
