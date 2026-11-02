"""Categories: the category table from a prequalification notice PDF."""

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from .. import categories as cat
from .. import category_llm as cl
from .common import Model, log

ENGINES = ("layout", "llm", "both")


@dataclass
class CategoriesResult:
    categories: List[cat.Category]
    issues: list                                    # category_llm.Issue
    out: str
    notice: Optional[object] = None                 # what the model read off the notice (llm, both)
    changes: List[str] = field(default_factory=list)   # against `diff`, when given


def extract(pdf: str, client: str, out: str, version: Optional[str] = None, engine: str = "layout",
            diff: Optional[str] = None, model: Model = Model()) -> CategoriesResult:
    """layout: table geometry, no network; llm: a model reads the PDF, code verifies every word
    against it; both: run both and reconcile."""
    issues, notice = [], None
    if engine in ("layout", "both"):
        cats = cat.extract_categories(pdf, client=client, version=version)
        problems = cat.check_sequence(cats)
        if engine == "layout" and (not cats or problems):
            log.info("the layout parser could not read this notice cleanly; "
                     "try --engine llm (or both to cross-check)")
    if engine in ("llm", "both"):
        ex = cl.LLMExtractor(model=model.id).extract(pdf, client=client, version=version)
        notice = n = ex.notice
        log.info(f"notice: {n.title or '?'} | client: {n.client_name or '?'} | closing: {n.closing_date or '?'}")
        if engine == "llm":
            cats, issues = ex.categories, ex.issues
        else:
            cats, issues = cl.reconcile(cats, ex.categories, cl.pdf_tokens(pdf))
            issues += [i for i in ex.issues if i.kind in ("kind", "no-text-layer")]
            if not version and n.closing_date:
                for c in cats:
                    c.version = f"closing {n.closing_date}"
    else:
        issues = [cl.Issue(None, "sequence", p) for p in problems]

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cat.save(cats, out)
    groups = Counter(c.group for c in cats)
    log.info(f"{len(cats)} categories in {len(groups)} groups -> {out}")
    for i in sorted(issues, key=lambda i: (i.kind != "sequence", i.number or 0)):
        where = f"#{i.number} " if i.number is not None else ""
        log.info(f"{i.kind.upper():13} {where}{i.message}")
    r = CategoriesResult(cats, issues, out, notice)
    if diff:
        r.changes = cat.diff(cat.load(diff), cats)
        log.info(f"\n{len(r.changes)} change(s) since {diff}:" if r.changes else "\nno changes")
        for c in r.changes:
            log.info(f"  {c}")
    return r


# --- the table generation reads, checked row by row --------------------------------

ERROR, WARNING = "error", "warning"


@dataclass
class RowProblem:
    number: int                     # the category's number in the table
    field: str                      # the column to fix
    severity: str                   # error: fix before generating (gate: the sheet is NOT WRITTEN); warning: a look
    message: str


def gate_problem(c: cat.Category) -> Optional[str]:
    """Spec 02 G7 / T19: the gate is TRUE exactly when individuals may apply."""
    from ..schema import INDIVIDUAL, business_types
    if c.gate_question == (INDIVIDUAL in business_types(c.applies_to)):
        return None
    return (f"categories table: Gate Question is {'TRUE' if c.gate_question else 'empty'} "
            f"but Applies To ({', '.join(c.applies_to) or 'empty'}) "
            f"{'lacks' if c.gate_question else 'includes'} {INDIVIDUAL}")


def normalise(c: cat.Category) -> cat.Category:
    """Applies To in the canonical names and order ('plc' -> Private Limited Company)."""
    from ..schema import business_types
    c.applies_to = business_types(c.applies_to)
    return c


def check_table(cats: List[cat.Category]) -> List[RowProblem]:
    from ..schema import BUSINESS_TYPES
    out = []
    seen = Counter(c.number for c in cats)
    for c in cats:
        if seen[c.number] > 1:
            out.append(RowProblem(c.number, "number", ERROR, f"number {c.number} is used {seen[c.number]} times: "
                                                             f"two sheets would get the same code"))
        if not c.description.strip():
            out.append(RowProblem(c.number, "description", ERROR, "no description"))
        g = gate_problem(c)
        if g:
            out.append(RowProblem(c.number, "gate_question", ERROR, g.removeprefix("categories table: ")))
        unknown = [b for b in c.applies_to if b not in BUSINESS_TYPES]
        if unknown:
            out.append(RowProblem(c.number, "applies_to", WARNING,
                                  f"{', '.join(unknown)}: not one of {', '.join(BUSINESS_TYPES)}"))
    return out


def import_client_list(path: str, client: str, current: List[cat.Category]) -> tuple:
    """The client's approved category list (.xlsx in the draft's layout) as the categories table
    (spec 17 D): numbers, descriptions as written (spacing tidied), groups and sub-categories from
    the file; a category whose description matches one in `current` keeps the team's settings for
    it (gate, Applies To, eligibility, licence, HR, technical, requirements). Returns (categories,
    one line per category whose settings were carried)."""
    from .. import refine as rf
    from ..schema import FIRMS, INDIVIDUAL
    tidy = lambda t: " ".join(str(t or "").replace("\xa0", " ").split())
    key = lambda t: tidy(t).lower()
    old = {key(c.description): c for c in current}
    out, kept = [], []
    for i, d in enumerate(rf.read_draft(path), 1):
        number = int(d.id) if str(d.id).isdigit() else i
        c = cat.Category(number, tidy(d.description), tidy(d.group) or None, [tidy(x) for x in d.sub_categories if tidy(x)],
                         client, None, "", [], d.individual, FIRMS + ([INDIVIDUAL] if d.individual else []),
                         d.licence, d.hr, d.technical)
        was = old.get(key(c.description))
        if was is not None:
            carried = [f for f in ("gate_question", "applies_to", "eligibility", "licence", "hr", "technical", "requirements")
                       if getattr(was, f) and getattr(was, f) != getattr(c, f)]
            for f in carried:
                setattr(c, f, getattr(was, f))
            if carried:
                kept.append(f"{c.number} {c.description[:50]}: {', '.join(carried)} kept from the table")
        out.append(normalise(c))
    return out, kept
