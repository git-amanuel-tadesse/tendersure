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
