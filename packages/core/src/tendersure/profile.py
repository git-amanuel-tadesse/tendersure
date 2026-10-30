"""A client's profile (spec 16 B): the house rules asked once, as a short form,
instead of learned from the client's comments.

The profile is stored as client rules (client_rules), so there is one
mechanism. Rules of the profile's shapes are read back into the form whether
the team wrote them by hand or through the form; saving the form replaces
them and keeps every other rule.
"""

import re
from dataclasses import asdict, dataclass, fields
from typing import List, Tuple

from .client_rules import Rule

OFFICE_MATCHES = ("physically registered office", "existence of office premises")
CONFLICT_MATCHES = ("associated with an employee", "conflict of interest form")
LOCAL_MATCHES = ("sort code", "IBAN")
REFS = ("Ref 1", "Ref 2", "Ref 3")


@dataclass
class Profile:
    licence_term: str = ""            # "trade license": business permit -> this
    purchase_order: str = ""          # "PO": LPO -> this
    purchase_order_except: str = ""   # groups it does not apply to, e.g. "engineering"
    local_suppliers: bool = False     # no sort code, no IBAN
    no_conflict_of_interest: bool = False
    office_evidence: str = ""         # the office evidence question's wording
    reference_marks: str = ""         # "15, 15, 10": Ref 1, Ref 2, Ref 3
    technical_limit: str = ""         # at most this many technical questions

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULTS = {"ET": Profile(licence_term="trade license", local_suppliers=True)}


def defaults(country: str) -> Profile:
    return Profile(**DEFAULTS[country].as_dict()) if country in DEFAULTS else Profile()


def _low(text: str) -> str:
    return " ".join(text.lower().split())


def _shape(r: Rule) -> str:
    """The profile field a rule fills, or '' when it is not one of the profile's shapes."""
    a, m, ungrouped = r.action, _low(r.match), not r.groups.strip()
    if a == "replace" and m == "business permit" and ungrouped:
        return "licence_term"
    if a == "replace" and m == "lpo" and all(g.strip().startswith("!") for g in r.groups.split(",") if g.strip()):
        return "purchase_order"
    if a == "remove" and m in {_low(x) for x in LOCAL_MATCHES} and ungrouped:
        return "local_suppliers"
    if a == "remove" and m in {_low(x) for x in CONFLICT_MATCHES} and ungrouped:
        return "no_conflict_of_interest"
    if a == "reword" and m in OFFICE_MATCHES and ungrouped:
        return "office_evidence"
    if a == "marks" and m in {_low(x) for x in REFS} and ungrouped:
        return "reference_marks"
    if a == "limit" and m == "technical" and ungrouped:
        return "technical_limit"
    return ""


def read(rules: List[Rule]) -> Tuple[Profile, bool]:
    """The profile the rules give, and whether any rule gave a field."""
    p, seen = Profile(), False
    refs = {}
    for r in rules:
        f = _shape(r)
        seen = seen or bool(f)
        if f == "licence_term":
            p.licence_term = r.value
        elif f == "purchase_order":
            p.purchase_order = r.value
            p.purchase_order_except = ", ".join(g.strip()[1:].strip() for g in r.groups.split(",") if g.strip())
        elif f in ("local_suppliers", "no_conflict_of_interest"):
            setattr(p, f, True)
        elif f == "office_evidence":
            p.office_evidence = r.value
        elif f == "reference_marks":
            refs[_low(r.match)] = r.value
        elif f == "technical_limit":
            p.technical_limit = r.value
    if refs:
        p.reference_marks = ", ".join(refs.get(_low(x), "") for x in REFS).rstrip(", ")
    return p, seen


def rules_of(p: Profile, client: str) -> List[Rule]:
    note = lambda what: f"profile: {what}"
    out = []
    if p.licence_term.strip():
        out.append(Rule(client, "replace", "business permit", p.licence_term.strip(), "", note("licence term")))
    if p.purchase_order.strip():
        groups = ", ".join(f"!{g.strip()}" for g in p.purchase_order_except.split(",") if g.strip())
        out.append(Rule(client, "replace", "LPO", p.purchase_order.strip(), groups, note("purchase order term")))
    if p.local_suppliers:
        out += [Rule(client, "remove", m, "", "", note("local suppliers: no " + m)) for m in LOCAL_MATCHES]
    if p.no_conflict_of_interest:
        out += [Rule(client, "remove", m, "", "", note("no conflict-of-interest question")) for m in CONFLICT_MATCHES]
    if p.office_evidence.strip():
        out += [Rule(client, "reword", m, p.office_evidence.strip(), "", note("office evidence")) for m in OFFICE_MATCHES]
    marks = [x.strip() for x in p.reference_marks.split(",")] if p.reference_marks.strip() else []
    total = sum(float(x) for x in marks if re.fullmatch(r"\d+(\.\d+)?", x))
    for ref, m in zip(REFS, marks):
        if m:
            out.append(Rule(client, "marks", ref, m, "", note(f"reference marks ({p.reference_marks.strip()} = {total:g})")))
    if p.technical_limit.strip():
        out.append(Rule(client, "limit", "technical", p.technical_limit.strip(), "", note("most technical questions")))
    return out


def save_into(p: Profile, rules: List[Rule], client: str) -> List[Rule]:
    """This client's rules with the profile's in place of those of its shapes; every other rule kept,
    in its order."""
    mine = [r for r in rules if r.client.upper() == client.upper()]
    others = [r for r in rules if r.client.upper() != client.upper()]
    key = lambda r: (r.action, _low(r.match), r.value.strip(), r.groups.replace(" ", ""))
    notes = {key(r): r.note for r in mine if _shape(r) and r.note}
    new = rules_of(p, client)
    for r in new:                                    # an unchanged rule keeps the team's own note
        r.note = notes.get(key(r), r.note)
    return others + [r for r in mine if not _shape(r)] + new


FIELDS = [f.name for f in fields(Profile)]
