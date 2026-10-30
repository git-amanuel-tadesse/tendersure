"""Criteria generation (spec 02): the categories table -> one criteria sheet per
category, the QA report, and the learned drafts to review.

A first build asks the model (when one is configured) for proposals; applying
a review is the same build with `proposals` (exactly what the reviewer saw,
no new model call) and `answers` (the Decisions tab, by item ID) given.
"""

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import brief as cb
from .. import parallel
from .. import categories as cat
from .. import gating
from .. import glossary as gl
from .. import naming
from .. import research as rs
from .. import trust
from .. import yesno
from ..generate import (Generator, MarksError, Target, ai_cache_key, check_total, finalise, one_label_per_section,
                        to_sheet)
from ..generate import apply_research as generate_research
from ..paths import current
from ..reports import load_decisions, write_qa_report
from ..requirements import stated as stated_requirements
from ..schema import business_types
from ..similarity import Retriever
from ..validate import validate_sheet
from ..writer import Writer
from .categories import gate_problem
from .common import FlowError, Model, audience, glossary, groups_for, load_source, log, read_marks, research_tools


@dataclass
class Settings:
    categories: str                         # the categories table (CSV or JSON)
    client: str                             # client code, e.g. ERCS
    prefix: str                             # sheet code prefix, e.g. ERCS-ET-2026-
    out: str                                # the workbook to write
    source: Optional[str] = None            # a folder of workbooks instead of the bank
    bank: Optional[str] = None
    client_name: Optional[str] = None       # name used inside question text, if not the code
    only: Optional[List[int]] = None        # category numbers
    decisions: Optional[str] = None         # bank.xlsx with the Decision column filled in
    marks: Optional[str] = None             # CSV of code,section,marks
    rulings: Optional[str] = None
    research: Optional[str] = None
    audience: Optional[str] = None
    yes_no: Optional[str] = None
    glossary: Optional[str] = None
    country: Optional[str] = None           # default: from the prefix (ERCS-ET-... -> ET)
    rebuild_existing: bool = False
    formulas: bool = False
    split_files: bool = False
    ai_review: bool = True                  # False: stop after the proposals (rewordings applied)
    model: Model = Model()
    proposals: Optional[Dict[str, dict]] = None   # by sheet code: replayed when applying a review
    answers: Optional[Dict[str, str]] = None      # by Decisions item ID; None: a first build
    brief: str = ""                         # the client's brief (spec 07 B), read first by the model steps
    benchmark: Optional[str] = None         # the client's sample criteria workbook (spec 08)
    benchmark_map: Optional[str] = None     # group | kind | tab; proposed (and written) when absent
    client_rules: Optional[str] = None      # spec 12 C; default data/client_rules.csv

    def __post_init__(self):
        p = current()
        self.bank = self.bank or p.bank
        self.rulings = self.rulings or p.learned("rulings")
        self.research = self.research or p.learned(rs.RESEARCH)
        self.audience = self.audience or p.learned("audience")
        self.yes_no = self.yes_no or p.learned("yesno_polarity")
        self.glossary = self.glossary or p.learned("glossary")
        self.client_rules = self.client_rules or p.learned("client_rules")

    @property
    def reviewing(self) -> bool:
        return self.answers is not None


@dataclass
class Result:
    plans: list                                   # the sheets written, as plans
    items: list                                   # the Decisions tab (review.Item)
    failed: List[Tuple[str, str]]                 # (code, why) for every sheet NOT WRITTEN
    files: List[str]                              # the workbook(s) written
    qa: str
    learned_review: Optional[str] = None
    review_log: List[str] = field(default_factory=list)
    review_problems: List[str] = field(default_factory=list)
    rules_checked: int = 0                        # client rules and category items checked (spec 16 A)
    rules_broken: List[str] = field(default_factory=list)   # "code: message" for each broken one

    @property
    def written(self) -> List[str]:
        return [p.target.code for p in self.plans]

    @property
    def open_items(self) -> list:
        return [i for i in self.items if not i.answer and not i.status]


def generate(s: Settings) -> Result:
    sheets, template_loader = load_source(s.source, s.bank)
    cats = cat.load(s.categories)
    if s.only:
        wanted = set(s.only)
        cats = [c for c in cats if c.number in wanted]
    groups = groups_for(sheets, cat.load(s.categories), s.client)
    decisions = load_decisions(s.decisions) if s.decisions else {}
    marks = read_marks(s.marks) if s.marks else {}
    grades = trust.grade(sheets)
    rulings, ruled = trust.load_rulings(s.rulings)
    gen = Generator(sheets, Retriever(sheets, groups), decisions, grades, rulings)

    refs = benchmark_refs(s, cats)
    from .. import client_rules as cr
    rules = cr.for_client(cr.load(s.client_rules), s.client) if os.path.exists(s.client_rules) else []
    if rules:
        log.info(f"client rules: {len(rules)} for {s.client} ({', '.join(sorted({r.action for r in rules}))})")
    plans, failed = [], []
    for c in list(cats):
        problem = gate_problem(c)                   # the same rule the categories page shows per row
        if problem:
            failed.append((c.code(s.prefix), problem))
            cats.remove(c)
    for c in cats:
        code = c.code(s.prefix)
        exclude = {sh.name for sh in sheets if sh.code == code} if s.rebuild_existing else None
        target = Target(code, c.description, s.client, c.group, s.client_name, c.sub_categories,
                        c.requirements or stated_requirements(c), c.eligibility, c.gate_question,
                        business_types(c.applies_to), c.licence, c.hr, c.technical, s.brief, rules,
                        s.country or rs.country_of(s.prefix))
        try:
            plan = gen.plan(target, exclude=exclude, marks=marks.get(code))
            plan.benchmark = refs[0].get(c.number)
            if c.number in refs[1]:
                plan.notes.append(refs[1][c.number])
            check_total(plan)
        except MarksError as e:
            failed.append((code, str(e)))
            continue
        plans.append(plan)

    # Model proposals: replayed when applying a review (exactly what the
    # reviewer saw, no new model call); otherwise asked for.
    if s.reviewing:
        stored = s.proposals or {}
        for p in plans:
            p.proposal = stored.get(p.target.code)
    use_model, why_not = s.model.wanted()
    if not use_model:
        log.info(f"building without AI: {why_not}" + (f"; {cb.NOT_APPLIED}" if s.brief else ""))
    ai_cache, save_ai_cache = {}, None
    if use_model:
        from ..selectors import PROMPT_VERSION, ModelSelector, build_prompt
        selector = ModelSelector(model=s.model.name)
        # Every AI answer is saved the moment it arrives: a rerun after a crash
        # (or with unchanged inputs) reuses it instead of paying again.
        cache_path = os.path.join(current().cache, f"build_{s.client}_{selector.llm.model.replace('/', '_')}"
                                                   f"_v{PROMPT_VERSION}.json")
        if os.path.exists(cache_path):
            with open(cache_path) as fh:
                ai_cache = json.load(fh)

        def save_ai_cache():
            os.makedirs(current().cache, exist_ok=True)
            with open(cache_path, "w") as fh:
                json.dump(ai_cache, fh)

        counts = {"asked": 0, "reused": 0}
        todo = []
        for p in plans:
            if p.same_category or p.proposal is not None:
                continue
            key = ai_cache_key(p, build_prompt(p))
            if key in ai_cache:
                p.proposal = json.loads(json.dumps(ai_cache[key]))
                counts["reused"] += 1
            else:
                todo.append((p, key))

        def proposed(item, prop, error):        # in this thread, as each proposal arrives
            p, key = item
            if error is not None:               # one failed category must not sink the batch
                log.info(f"{p.target.code}: no model proposal ({error})")
                return
            ai_cache[key] = json.loads(json.dumps(prop))
            save_ai_cache()
            counts["asked"] += 1
            log.info(f"{p.target.code}: {prop['model']} rewords {len(prop.get('reword', {}))} (applied), "
                     f"proposes {len(prop['drop'])} removal(s), "
                     f"{len(prop['add'])} addition(s), {len(prop['drafts'])} new question(s)"
                     + (f"; ignored {len(prop['ignored'])} invalid" if prop["ignored"] else "")
                     + f" [{counts['asked']}/{len(todo)}]")
        if todo:
            log.info(f"model proposals: asking for {len(todo)}, {parallel.workers()} at a time")
        parallel.each(todo, lambda item: selector.propose(item[0]), proposed)
        log.info(f"model proposals: {counts['asked']} new, {counts['reused']} reused from earlier runs")
        for p in plans:
            for x in (p.proposal or {}).get("brief_not_applied", []):
                p.notes.append(f"the client's brief asked: {x} (not applied: the template rules come first)")

    items, review_log, review_problems = None, [], []
    if use_model and not s.reviewing:
        if not s.ai_review:
            from ..review import auto_reword
            items, n = auto_reword(plans)
            if n:
                log.info(f"reworded {n} copied question(s); removals and additions wait in the QA file")
        else:
            from ..reviewer import ModelReviewer, ai_review
            log.info("AI review: a second pass checks every proposal; only what both agree on is applied")
            items, summary = ai_review(gen, plans, ModelReviewer(), cache=ai_cache, save=save_ai_cache)
            log.info(f"AI review: {summary['answered']} decisions answered, {summary['disagreements']} "
                     f"disagreement(s) kept as the client's past sheet had it, "
                     f"{len(summary['rebalanced'])} sheet(s) rebalanced to 100 (all recorded in the QA file)")
            new_rulings = [i.ruling for i in items if i.ruling]
            if new_rulings:
                trust.save_rulings(s.rulings, new_rulings)
                log.info(f"saved {len(new_rulings)} client convention(s) settled by majority to {s.rulings}")
    if s.reviewing:
        from ..review import apply_answers
        items, review_log, review_problems = apply_answers(plans, s.answers, ruled)
        new_rulings = [i.ruling for i in items if i.ruling]
        if new_rulings:
            trust.save_rulings(s.rulings, new_rulings)
            log.info(f"saved {len(new_rulings)} standing ruling(s) to {s.rulings}")
        for line in review_log:
            log.info(f"review: {line}")
        for line in review_problems:
            log.info(f"REVIEW PROBLEM: {line}")

    country = s.country or rs.country_of(s.prefix)
    r_table, r_bank, r_web = research_tools(s.research, sheets, s.model) if use_model or os.path.exists(s.research) \
        else (rs.Table([]), None, None)
    known = {e.acronym for e in gl.load(s.glossary)} if os.path.exists(s.glossary) else set()
    rs.batch_research(plans, country, r_table, r_bank, r_web, s.client, known)
    for p in plans:
        generate_research(p, r_table, country)
    if r_table.findings:
        rs.save(r_table, s.research)
    book_audience = audience(s.audience, sheets)
    gated = [p for p in plans if p.target.gate_question]
    if gated and not book_audience.entries:          # spec 16 G: say when learned knowledge is missing
        warn = ("Applies To from built-in rules only: no past sheet in the bank fills Applies To, so who answers "
                "what was not learned (check firms, companies and individuals on this sheet)")
        log.info(f"audience: {len(gated)} gated sheet(s), and the audience table is empty - {warn}")
        for p in gated:
            p.notes.append(warn)
    donor_rows = gating.donors(trust.usable(sheets, grades), book_audience)
    for p in plans:
        gating.apply(p, book_audience, donor_rows)
    for p in plans:
        finalise(p)
    n = one_label_per_section(plans)
    if n:
        log.info(f"sections: {n} row(s) relabelled so every sheet spells each section the same way")
    book = yesno.load(s.yes_no)
    judge = yesno.ModelJudge(model=s.model.name) if use_model else yesno.RuleJudge()
    judged = yesno.settle(plans, book, judge)
    if judged:
        yesno.save(book, s.yes_no)
        bad = sum(v.yes_is == "bad" for v in judged)
        log.info(f"yes/no: judged {len(judged)} scored questions ({bad} where Yes is bad; "
                 f"{sum(v.status == yesno.RULE_DRAFTED for v in judged)} by keywords), saved as drafts in "
                 f"{s.yes_no}: confirm or change them there")
    names = glossary(s.glossary, sheets)
    drafter = naming.ModelDrafter(model=s.model.name, examples=names.entries) if use_model \
        else naming.RuleDrafter()
    drafted = naming.name_documents(plans, names, drafter)
    from .. import learned_review as lr
    base = os.path.splitext(s.out)[0]
    os.makedirs(os.path.dirname(os.path.abspath(s.out)), exist_ok=True)
    learned = base + "_learned_review.xlsx"
    n, y = lr.write_review(names, book, learned)
    if n or y:
        log.info(f"learned drafts waiting for review: {n} name lines, {y} Yes/No lines -> "
                 f"{learned} (answer it, then: learn --review <file>)")
    if drafted:
        gl.save(names.entries, s.glossary)
        by_rules = sum(e.status == gl.RULE_DRAFTED for e in drafted)
        log.info(f"glossary: named {len(drafted)} new documents ({by_rules} short names drafted by rules, "
                 f"the rest given or drafted by the model; acronyms by the letter rule), saved as drafts in "
                 f"{s.glossary}: confirm or change them there")

    every_proposal = {p.target.code: p.proposal for p in plans}      # kept for NOT WRITTEN sheets too
    # Spec 02 G11: every sheet passes the check's content rules before it is
    # written; a sheet with an error is listed, not written.
    from .. import check as ck
    by_code = {c.code(s.prefix): c for c in cats}

    def yes_is(rows, i):
        v = book.lookup(trust.context_key(rows, i))
        return v.yes_is if v else None
    for p in list(plans):
        errors = [f for f in ck.sheet_findings(to_sheet(p), s.client, by_code.get(p.target.code), True, yes_is)
                  if f.severity == "error"]
        if errors:
            plans.remove(p)
            counts = Counter(f.rule for f in errors)
            failed.append((p.target.code, f"{len(errors)} check error(s): " + ", ".join(
                f"{r} {n}" for r, n in counts.most_common()) + f" (first: {errors[0].message})"))

    # Whatever happened above, a sheet that does not total 100 is listed, not
    # written - and never stops the rest of the batch.
    for p in list(plans):
        try:
            check_total(p)
        except MarksError as e:
            plans.remove(p)
            failed.append((p.target.code, str(e)))

    if not plans:
        raise FlowError("nothing to write: " + "; ".join(m for _, m in failed))
    writer = Writer(formulas=s.formulas, template_loader=template_loader)
    if s.split_files:
        files = []
        for p in plans:
            files.append(f"{base}_{p.target.code}.xlsx")
            writer.write([p], files[-1])
        log.info(f"wrote {len(plans)} files {base}_<code>.xlsx")
    else:
        files = [s.out]
        writer.write(plans, s.out)
        log.info(f"wrote {len(plans)} sheets -> {s.out}")
    from ..review import CHECK, decision_items, finding_items
    items = items if items is not None else decision_items(plans)
    # The findings cards were made before the template rules ran: make them again from the sheets
    # as written, so none reports a row those rules fixed (PSI: 11x "no options" in clean sheets).
    # Spec 16 A: the client's rules and the category's items, checked on every sheet as written; a
    # broken one is a finding card, so the run is not Ready.
    from ..rule_checks import check_rules
    broken, checked = [], 0
    for p in plans:
        sheet = to_sheet(p)
        p.findings = validate_sheet(sheet)
        c = by_code.get(p.target.code)
        checked += len([r for r in rules if c is None or r.in_group(c.group)]) + (
            len(c.technical) + len(c.hr) + len(c.licence) if c is not None else 0)
        found = [f for f in check_rules(sheet, c, rules) if f.severity == "error"]
        p.findings += found
        broken += [f"{p.target.code}: {f.message}" for f in found]
    items = [i for i in items if i.type != CHECK] + [i for i in finding_items(plans) if i.type == CHECK]
    brief_note = None if not s.brief else \
        f"Client's brief (read first by the model): {s.brief}" if use_model else f"{cb.NOT_APPLIED[0].upper()}{cb.NOT_APPLIED[1:]}."
    qa = write_qa_report(plans, base + "_qa.xlsx", items, brief_note, every_proposal)
    r = Result(plans, items, failed, files, qa, learned if n or y else None, review_log, review_problems,
               checked, broken)
    log.info(f"client rules: {checked} checked on the sheets written, "
             + (f"{len(broken)} broken (see the F- cards)" if broken else "all hold"))
    kinds = Counter(i.id[0] for i in r.open_items)
    log.info(f"wrote {qa}")
    log.info(f"Decisions tab: {len(items)} decisions, {len(items) - len(r.open_items)} settled; open: "
             f"{kinds['Q']} questions, {kinds['A']} additions, {kinds['M']} marks, "
             f"{kinds['C']} client conventions to settle, {kinds['N']} categories with no source, "
             f"{kinds['T'] + kinds['F']} team-wide")
    for code, msg in failed:
        log.info(f"NOT WRITTEN {code}: {msg}")
    return r


def benchmark_refs(s: Settings, cats) -> Tuple[Dict[int, dict], Dict[int, str]]:
    """What each category's sheet is compared with (spec 08): its group's
    tab, and why the others are not compared. The map is proposed where it
    has no line for a group, and saved so the team can correct it."""
    if not s.benchmark:
        return {}, {}
    from .. import benchmark as bm
    if not os.path.exists(s.benchmark):
        raise FlowError(f"no benchmark workbook at {s.benchmark}")
    tabs = bm.load(s.benchmark, s.client)
    for line in bm.report(tabs):
        log.info(line)
    path = s.benchmark_map or os.path.splitext(s.out)[0] + "_benchmark_map.csv"
    known = bm.load_map(path)
    lines = bm.propose_map(cats, tabs, s.client, known)
    if len(lines) > len(known):
        bm.save_map(lines, path)
        log.info(f"benchmark map: {len(lines) - len(known)} group line(s) proposed -> {path} "
                 f"(correct a line there and run again; your lines are kept)")
    use, why = bm.targets(cats, tabs, lines)
    log.info(f"benchmark: {len(use)} categories compared with a benchmark tab, {len(why)} not compared")
    return {n: bm.reference(t) for n, t in use.items()}, why


def read_review(qa: str) -> Tuple[Dict[str, dict], Dict[str, str]]:
    """(proposals, answers) from an answered QA workbook: what the reviewer saw, and what they said."""
    from ..review import read_answers, read_proposals
    return read_proposals(qa), read_answers(qa)
