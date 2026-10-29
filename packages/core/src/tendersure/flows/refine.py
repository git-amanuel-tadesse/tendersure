"""Category refinement (spec 01): a draft list -> revised list -> final list.

Each step works on drafts and lines in memory (`propose`, `apply`), so an app
can hold the review itself; `propose_file` and `apply_file` are the same
steps reading and writing the workbooks the team sends to the client.
"""

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import categories as cat
from .. import refine as rf
from .. import research as rs
from ..paths import current
from ..privacy import register_clients
from .common import Model, load_source, log, past_categories, research_tools

Target = Optional[Tuple[int, int]]


@dataclass
class ProposeResult:
    drafts: List[rf.Draft]
    lines: List[rf.Line]
    target: Target
    out: Optional[str] = None

    @property
    def proposed(self) -> int:
        return sum(ln.status != rf.REPEATED for ln in self.lines)

    @property
    def statuses(self) -> Dict[str, int]:
        return dict(Counter(ln.status for ln in self.lines).most_common())

    @property
    def questions(self) -> int:
        return sum(bool(ln.question) for ln in self.lines)


@dataclass
class ApplyResult:
    drafts: List[rf.Draft]
    final: List[rf.Line]
    excluded: List[rf.Line]
    notes: List[Tuple[rf.Line, str, str]]     # (line, client comment, the model's proposed change)
    problems: List[str]
    out: Optional[str] = None
    categories_out: Optional[str] = None
    categories: List[cat.Category] = field(default_factory=list)


def propose(drafts: List[rf.Draft], client: str, target: Target = None, bank: Optional[str] = None,
            research: Optional[str] = None, country: str = "", model: Model = Model()) -> List[rf.Line]:
    """The revised list: one line per proposed category, every draft row accounted for."""
    bank = bank or current().bank
    research = research or current().learned(rs.RESEARCH)
    register_clients([client])
    use_model, why_not = model.wanted()
    if not use_model:
        log.info(f"refine: without a model ({why_not}): duplicates folded, overlaps raised as questions")
    raw = rf.ModelRefiner(model=model.name).propose(drafts, client, past_categories(bank), target) \
        if use_model else None
    lines = rf.check(raw, drafts, rf.groups_of(drafts))
    table, kb, web = research_tools(research, load_source(None, bank)[0] if os.path.exists(bank) else [], model)
    rs.research([ln.description for ln in lines if ln.status != rf.REPEATED], country,
                ["names", "licences"], table, kb, web, client)
    rf.attach_research(lines, table, country)
    rs.save(table, research)
    return lines


def propose_file(draft: str, client: str, sheet: Optional[str] = None, target: Target = None,
                 out: Optional[str] = None, bank: Optional[str] = None, research: Optional[str] = None,
                 country: str = "", model: Model = Model()) -> ProposeResult:
    """Step A from a draft workbook (or CSV) to `<draft> - Revised.xlsx`."""
    drafts = rf.read_draft(draft, sheet)
    lines = propose(drafts, client, target, bank, research, country, model)
    out = out or os.path.join(current().reports, os.path.splitext(os.path.basename(draft))[0] + " - Revised.xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    rf.write_revised(out, drafts, lines, client, target)
    r = ProposeResult(drafts, lines, target, out)
    log.info(f"refine: {len(drafts)} draft rows -> {r.proposed} proposed categories"
             + (f" (target {target[0]}-{target[1]})" if target else "") + "; "
             + ", ".join(f"{n} {s}" for s, n in r.statuses.items())
             + f"; {r.questions} questions for the client -> {out}")
    return r


def apply(drafts: List[rf.Draft], lines: List[rf.Line], client: str, version: Optional[str] = None,
          model: Model = Model()) -> ApplyResult:
    """Step B: the review's decisions applied; client comments get a proposed change, never applied."""
    register_clients([client])
    use_model, _ = model.wanted()
    notes = [(ln, ln.comment) for ln in lines if ln.comment and ln.decision != "edit"]
    final, excluded, problems = rf.apply_review(drafts, lines)
    changes = rf.ModelRefiner(model=model.name).interpret(notes, client) if use_model and notes \
        else ["" for _ in notes]
    return ApplyResult(drafts, final, excluded, [(ln, c, ch) for (ln, c), ch in zip(notes, changes)], problems,
                       categories=rf.to_categories(final, client, version))


def apply_file(revised: str, client: str, out: Optional[str] = None, categories_out: Optional[str] = None,
               version: Optional[str] = None, model: Model = Model()) -> ApplyResult:
    """Step B from an answered revised workbook to the final list (and the categories table)."""
    drafts, lines = rf.read_revised(revised)
    r = apply(drafts, lines, client, version, model)
    r.out = out or os.path.splitext(revised)[0] + "_final.xlsx"
    rf.write_final(r.out, r.final, r.notes)
    log.info(f"refine: {len(drafts)} draft rows -> {len(r.final)} final categories "
             f"({len(r.excluded)} lines removed, rejected or folded); {len(r.notes)} client comments listed -> {r.out}")
    if categories_out:
        cat.save(r.categories, categories_out)
        r.categories_out = categories_out
        log.info(f"refine: categories table -> {categories_out} (the input of generate)")
    for p in r.problems:
        log.info(f"REVIEW PROBLEM: {p}")
    return r


def evaluate(workbook: str, draft_tab: str, final_tab: str, client: str, out: Optional[str] = None,
             bank: Optional[str] = None, model: Model = Model()) -> Dict:
    """Compare a proposal with the list a team and client settled on (spec 01, NRC history)."""
    bank = bank or current().bank
    register_clients([client])
    drafts = rf.read_draft(workbook, draft_tab)
    team = [d.description for d in rf.read_draft(workbook, final_tab)]
    use_model, why_not = model.wanted()
    raw = rf.ModelRefiner(model=model.name).propose(drafts, client, past_categories(bank), None) \
        if use_model else None
    lines = rf.check(raw, drafts, rf.groups_of(drafts))
    if out:
        rf.write_revised(out, drafts, lines, client)
    log.info(f"refine-evaluate ({'model' if use_model else 'rules: ' + why_not}):")
    report = rf.evaluate(drafts, lines, team)
    for k, v in report.items():
        if isinstance(v, list):
            log.info(f"  {k}: {len(v)}")
            for x in v:
                log.info(f"      {x}")
        else:
            log.info(f"  {k}: {v}")
    return report
