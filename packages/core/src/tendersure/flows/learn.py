"""Learning from the bank (spec 04): names and Yes/No verdicts for every
question, the glossary, and the review of what was drafted."""

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from .. import gating
from .. import glossary as gl
from .. import learned_review as lr
from .. import naming
from .. import trust
from .. import yesno
from ..learned_store import CsvStore
from .common import Model, load_source, log


@dataclass
class LearnResult:
    review: str                         # the review workbook written
    name_lines: int                     # waiting for review
    yes_no_lines: int
    applied: int = 0                    # answered lines applied (learn --review)
    problems: List[str] = field(default_factory=list)


def learn(store: Optional[str], out: str, source: Optional[str] = None, bank: Optional[str] = None,
          model: Model = Model()) -> LearnResult:
    """Name and judge every question in the history not seen before; write the review."""
    from ..paths import current
    st = CsvStore(store)
    book = yesno.load(st)
    sheets, _ = load_source(source, bank or current().bank)
    usable = trust.usable(sheets, trust.grade(sheets))
    g = gl.Glossary(gl.reseed(gl.load(st), gl.seed(usable)))
    use_model, why_not = model.wanted()
    if not use_model:
        log.info(f"learn: without a model ({why_not}): drafts by rules")
    drafter = naming.ModelDrafter(model=model.name, examples=g.entries) if use_model \
        else naming.RuleDrafter()
    judge = yesno.ModelJudge(model=model.name) if use_model else yesno.RuleJudge()
    history = [(s.client, s.rows) for s in usable]
    named, (rebuilt, reused, built) = naming.learn(history, g, drafter)
    judged = yesno.learn(history, book, judge)
    audience = gating.reseed(gating.load(st), gating.seed(usable))
    gl.save(g.entries, st)
    yesno.save(book, st)
    gating.save(audience, st)
    similar = sum(e.status == gl.SIMILAR for e in named) + sum(v.status == yesno.SIMILAR for v in judged)
    log.info(f"learn: {len(usable)} usable sheets; names: {len(named)} questions newly named, {rebuilt + reused + built} "
             f"acronyms filled or rebuilt; Yes/No: {len(judged)} questions judged; {similar} taken from a "
             f"confirmed rewording; audience: {len(audience)} questions")
    return _write(g, book, out)


def apply_review(review: str, store: Optional[str], out: str) -> LearnResult:
    """The answered review: each answered line confirms its group's names or verdict."""
    st = CsvStore(store)
    book = yesno.load(st)
    g = gl.Glossary(gl.load(st))
    applied, problems = lr.apply_review(review, g, book)
    gl.save(g.entries, st)
    yesno.save(book, st)
    log.info(f"learn: applied {applied} answered lines from {review}")
    for p in problems:
        log.info(f"REVIEW PROBLEM: {p}")
    r = _write(g, book, out)
    r.applied, r.problems = applied, problems
    return r


def _write(g, book, out: str) -> LearnResult:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    n, y = lr.write_review(g, book, out)
    log.info(f"learn: {n} name lines and {y} Yes/No lines wait for review -> {out}"
             + ("  (answer the Decision column, then: learn --review <file>)" if n or y else ""))
    return LearnResult(out, n, y)


@dataclass
class GlossaryResult:
    out: str
    entries: int
    both_names: int
    kept: int                           # confirmed or drafted rows kept from the team's file


def seed_glossary(out: str, source: Optional[str] = None, bank: Optional[str] = None, fill: bool = False,
                  model: Model = Model()) -> GlossaryResult:
    """Seed the glossary from the bank, keeping the team's confirmed and drafted rows."""
    from ..paths import current
    sheets, _ = load_source(source, bank or current().bank)
    usable = trust.usable(sheets, trust.grade(sheets))
    fresh = gl.seed(usable)
    existing = gl.load(out) if os.path.exists(out) else []
    entries = gl.reseed(existing, fresh)
    if fill:
        use_model, why_not = model.wanted()
        if not use_model:
            log.info(f"glossary: drafting by rules ({why_not})")
        drafter = naming.ModelDrafter(model=model.name, examples=entries) if use_model \
            else naming.RuleDrafter()
        rederived, reused, built = naming.complete_glossary(gl.Glossary(entries), drafter)
        log.info(f"glossary: {rederived} drafted acronyms rebuilt by the letter rule; empty acronyms: {reused} "
                 f"taken from the same name elsewhere, {built} built by the rule; filled rows are drafts to confirm")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    gl.save(entries, out)
    kept = sum(e.status != gl.SEEDED for e in entries)
    full = sum(bool(e.short_name and e.acronym) for e in entries)
    clients = Counter(e.client for e in entries)
    log.info(f"glossary: {len(entries)} entries from {len(usable)} usable sheets "
             f"({', '.join(f'{c} {n}' for c, n in sorted(clients.items()))}); "
             f"{full} with both names, {len(entries) - full} with one; {kept} confirmed or drafted kept -> {out}")
    return GlossaryResult(out, len(entries), full, kept)
