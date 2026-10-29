"""Criteria generation (spec 02): the categories table -> one criteria sheet per
category, the QA report, and the learned drafts to review.

A first build asks the model (when one is configured) for proposals; applying
a review is the same build with `proposals` (exactly what the reviewer saw,
no new model call) and `answers` (the Decisions tab, by item ID) given.
"""

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import categories as cat
from .. import gating
from .. import glossary as gl
from .. import naming
from .. import research as rs
from .. import trust
from .. import yesno
from ..generate import Generator, MarksError, Target, check_total, finalise, to_sheet
from ..generate import apply_research as generate_research
from ..paths import current
from ..reports import load_decisions, write_qa_report
from ..schema import INDIVIDUAL, business_types
from ..similarity import Retriever
from ..writer import Writer
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

    def __post_init__(self):
        p = current()
        self.bank = self.bank or p.bank
        self.rulings = self.rulings or p.learned("rulings")
        self.research = self.research or p.learned(rs.RESEARCH)
        self.audience = self.audience or p.learned("audience")
        self.yes_no = self.yes_no or p.learned("yesno_polarity")
        self.glossary = self.glossary or p.learned("glossary")

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

    plans, failed = [], []
    for c in list(cats):
        # spec 02 G7 / T19: the gate is TRUE exactly when individuals may apply
        if c.gate_question != (INDIVIDUAL in business_types(c.applies_to)):
            failed.append((c.code(s.prefix), f"categories table: Gate Question is {'TRUE' if c.gate_question else 'empty'} "
                                             f"but Applies To ({', '.join(c.applies_to) or 'empty'}) "
                                             f"{'lacks' if c.gate_question else 'includes'} {INDIVIDUAL}"))
            cats.remove(c)
    for c in cats:
        code = c.code(s.prefix)
        exclude = {sh.name for sh in sheets if sh.code == code} if s.rebuild_existing else None
        target = Target(code, c.description, s.client, c.group, s.client_name, c.sub_categories,
                        c.requirements, c.eligibility, c.gate_question, business_types(c.applies_to))
        try:
            plan = gen.plan(target, exclude=exclude, marks=marks.get(code))
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
        log.info(f"building without AI: {why_not}")
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

        asked = reused = 0
        for p in plans:
            if p.same_category or p.proposal is not None:
                continue
            key = f"{p.target.code}|{hashlib.sha1(build_prompt(p).encode()).hexdigest()[:12]}"
            if key in ai_cache:
                p.proposal = json.loads(json.dumps(ai_cache[key]))
                reused += 1
                continue
            try:
                prop = selector.propose(p)
            except Exception as e:      # one failed category must not sink the batch
                log.info(f"{p.target.code}: no model proposal ({e})")
                continue
            ai_cache[key] = json.loads(json.dumps(prop))
            save_ai_cache()
            asked += 1
            log.info(f"{p.target.code}: {prop['model']} rewords {len(prop.get('reword', {}))} (applied), "
                     f"proposes {len(prop['drop'])} removal(s), "
                     f"{len(prop['add'])} addition(s), {len(prop['drafts'])} new question(s)"
                     + (f"; ignored {len(prop['ignored'])} invalid" if prop["ignored"] else ""))
        log.info(f"model proposals: {asked} new, {reused} reused from earlier runs")

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
    donor_rows = gating.donors(trust.usable(sheets, grades), book_audience)
    for p in plans:
        gating.apply(p, book_audience, donor_rows)
    for p in plans:
        finalise(p)
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
            rules = Counter(f.rule for f in errors)
            failed.append((p.target.code, f"{len(errors)} check error(s): " + ", ".join(
                f"{r} {n}" for r, n in rules.most_common()) + f" (first: {errors[0].message})"))

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
    from ..review import decision_items
    items = items if items is not None else decision_items(plans)
    qa = write_qa_report(plans, base + "_qa.xlsx", items)
    r = Result(plans, items, failed, files, qa, learned if n or y else None, review_log, review_problems)
    kinds = Counter(i.id[0] for i in r.open_items)
    log.info(f"wrote {qa}")
    log.info(f"Decisions tab: {len(items)} decisions, {len(items) - len(r.open_items)} settled; open: "
             f"{kinds['Q']} questions, {kinds['A']} additions, {kinds['M']} marks, "
             f"{kinds['C']} client conventions to settle, {kinds['N']} categories with no source, "
             f"{kinds['T'] + kinds['F']} team-wide")
    for code, msg in failed:
        log.info(f"NOT WRITTEN {code}: {msg}")
    return r
