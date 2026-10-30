"""Client rules (spec 12 C): what a client wants changed on every sheet, or on
some groups' sheets, said once and applied on every run.

    remove   the questions whose wording contains `match`
    replace  the term `match` with `value` ("LPO" -> "PO", "business permit" -> "trade license")
    reword   a question containing `match` gets `value` as its whole wording
    limit    at most `value` questions in the section named `match`
    marks    the sub-section named `match` ("Ref 1") carries `value` marks; the
             other sections take up the difference (spec 14 C)

`groups` scopes a rule by the words of a category's group: "engineering" for
those groups only, "!engineering" for every other; empty for all. Matching is
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
ACTIONS = ("remove", "replace", "reword", "limit", "marks")
NEVER = {"declaration", "submission"}


@dataclass
class Rule:
    client: str
    action: str
    match: str
    value: str = ""
    groups: str = ""
    note: str = ""

    def in_group(self, group: Optional[str]) -> bool:
        words = set(re.findall(r"[a-z]+", (group or "").lower()))
        terms = [t.strip().lower() for t in self.groups.split(",") if t.strip()]
        include = [t for t in terms if not t.startswith("!")]
        exclude = [t[1:].strip() for t in terms if t.startswith("!")]
        hit = lambda t: set(re.findall(r"[a-z]+", t)) <= words
        return (not include or any(hit(t) for t in include)) and not any(hit(t) for t in exclude)

    def pattern(self) -> re.Pattern:
        words = [re.escape(w) for w in self.match.split()]
        return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(words) + r"(?![A-Za-z0-9])", re.I)

    def problem(self) -> Optional[str]:
        """Why the rule cannot be applied, if it cannot."""
        if self.action not in ACTIONS:
            return f"action '{self.action}' is not one of {', '.join(ACTIONS)}"
        if not self.match.strip():
            return "no match given"
        if self.action in ("replace", "reword") and not self.value.strip():
            return f"{self.action} needs a value"
        if self.action == "limit" and not self.value.strip().isdigit():
            return "limit needs a number as value"
        if self.action == "marks" and not re.fullmatch(r"\d+(\.\d+)?", self.value.strip()):
            return "marks needs a number as value"
        return None


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
    from .generate import NEW, drop_empty_sections, drop_row, keep_section_totals, rebalance_to_100
    mine = [r for r in rules if r.in_group(plan.target.group) and r.action != "marks"]
    if not mine:
        return
    before = {k: v[0] for k, v in plan.section_marks().items()}
    changed = set()
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
            section = [p for p in rows if section_key(p.row.section) == key]
            if len(section) <= n:
                continue
            # scored questions first, then the sheet's own, then those added by the model or research
            rank = sorted(section, key=lambda p: ((p.row.marks or 0) <= 0, p.origin == NEW, section.index(p)))
            keep = set(map(id, rank[:n]))
            for p in [p for p in section if id(p) not in keep]:
                changed.add(key)
                drop_row(plan, plan.rows.index(p), f"{why} (at most {n} {key} questions)")
    if changed:
        drop_empty_sections(plan)
        here = {section_key(p.row.section) for p in plan.questions}
        problems = keep_section_totals(plan, {k: before.get(k, 0) for k in changed if k in here}, "client rules")
        if problems or any(k not in here and before.get(k) for k in changed):
            rebalance_to_100(plan, "client rules")


def _name(text) -> str:
    return "".join(str(text or "").lower().split())


def apply_marks(plan, rules: List[Rule]):
    """The `marks` rules on one plan (spec 14 C), after every other template rule: each named
    sub-section carries its marks, and the sections no rule set take up the difference."""
    from .generate import rebalance_others, share_marks
    from .validate import asks_for_document
    mine = [r for r in rules if r.action == "marks" and r.in_group(plan.target.group)]
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
    mine = {_name(r.match): float(r.value) for r in rules if r.action == "marks" and r.in_group(plan.target.group)}
    out = {}
    for key in {section_key(p.row.section) for p in plan.questions}:
        rows = [p.row for p in plan.questions if section_key(p.row.section) == key]
        named = {_name(r.sub_section) for r in rows} & set(mine)
        if named and all(_name(r.sub_section) in named for r in rows if r.marks):
            out[key] = sum(mine[n] for n in named)
    return out
