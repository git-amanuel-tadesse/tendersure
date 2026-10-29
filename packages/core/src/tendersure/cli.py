"""Command line: python -m tendersure <command> ...

  ingest      add a folder of workbooks to the bank (data/bank.sqlite; only
              new or changed files are read) and write the reports
  categories  extract the category table from a prequalification notice PDF
  generate    build criteria sheets from the bank for a notice's categories,
              rewording copied questions (with a model), plus a QA report
  evaluate    leave-one-out check against the sheets the team actually built
"""

import argparse
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from typing import List

from . import categories as cat
from . import glossary as gl
from . import gating
from . import naming
from . import research as rs
from . import yesno
from . import category_llm as cl
from .evaluate import evaluate, summarise
from .llm import LLMError
from .paths import current
from .privacy import PrivacyError
from .generate import Generator, Target
from .parser import Sheet
from .similarity import Retriever
from . import trust
from . import flows
from .flows.common import groups_for, past_categories, research_tools


def _note_source(a):
    src = getattr(a, "source", None)
    if src and os.path.isdir(src):
        print(f"note: reading workbooks straight from '{src}'; run `ingest` to put them in the bank",
              file=sys.stderr)


def load_source(a):
    """Sheets for a command: from the bank (default), or straight from a folder.

    Returns (sheets, template_loader for the writer)."""
    _note_source(a)
    return flows.load_source(getattr(a, "source", None), a.bank)


def cmd_ingest(a):
    flows.ingest(a.corpus, a.bank, a.out)


def cmd_categories(a):
    _need(a.pdf, "notice PDF", "check the path; quote it if it contains spaces")
    issues = []
    if a.engine in ("layout", "both"):
        cats = cat.extract_categories(a.pdf, client=a.client, version=a.version)
        problems = cat.check_sequence(cats)
        if a.engine == "layout" and (not cats or problems):
            print("the layout parser could not read this notice cleanly; "
                  "try --engine llm (or both to cross-check)")
    if a.engine in ("llm", "both"):
        ex = cl.LLMExtractor(model=a.model).extract(a.pdf, client=a.client, version=a.version)
        n = ex.notice
        print(f"notice: {n.title or '?'} | client: {n.client_name or '?'} | closing: {n.closing_date or '?'}")
        if a.engine == "llm":
            cats, issues = ex.categories, ex.issues
        else:
            cats, issues = cl.reconcile(cats, ex.categories, cl.pdf_tokens(a.pdf))
            issues += [i for i in ex.issues if i.kind in ("kind", "no-text-layer")]
            if not a.version and n.closing_date:
                for c in cats:
                    c.version = f"closing {n.closing_date}"
    else:
        issues = [cl.Issue(None, "sequence", p) for p in problems]

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    cat.save(cats, a.out)
    groups = Counter(c.group for c in cats)
    print(f"{len(cats)} categories in {len(groups)} groups -> {a.out}")
    for i in sorted(issues, key=lambda i: (i.kind != "sequence", i.number or 0)):
        where = f"#{i.number} " if i.number is not None else ""
        print(f"{i.kind.upper():13} {where}{i.message}")
    if a.diff:
        changes = cat.diff(cat.load(a.diff), cats)
        print(f"\n{len(changes)} change(s) since {a.diff}:" if changes else "\nno changes")
        for c in changes:
            print(f"  {c}")


def _need(path: str, what: str, how: str):
    if path and not os.path.exists(path):
        sys.exit(f"{what} not found: {path}\n  {how}")


CATEGORIES_HOW = ('create it first with the categories step, e.g.\n'
                  '  python -m tendersure categories "<notice.pdf>" --client ERCS --out data/ercs_categories.csv')


def _model(a) -> flows.Model:
    return flows.Model(a.model, getattr(a, "no_model", False))


def _model_wanted(a):
    """The AI runs by default whenever a model is configured; --no-model turns it off."""
    return _model(a).wanted()


def cmd_learn(a):
    """Learn names and Yes/No verdicts for every question in the bank (spec 04)."""
    from . import learned_review as lr
    from .learned_store import CsvStore
    store = CsvStore(a.store)
    book = yesno.load(store)
    if a.review:
        g = gl.Glossary(gl.load(store))
        applied, problems = lr.apply_review(a.review, g, book)
        gl.save(g.entries, store)
        yesno.save(book, store)
        print(f"learn: applied {applied} answered lines from {a.review}")
        for p in problems:
            print(f"REVIEW PROBLEM: {p}")
    else:
        sheets, _ = load_source(a)
        usable = trust.usable(sheets, trust.grade(sheets))
        g = gl.Glossary(gl.reseed(gl.load(store), gl.seed(usable)))
        use_model, why_not = _model_wanted(a)
        if not use_model:
            print(f"learn: without a model ({why_not}): drafts by rules")
        drafter = naming.ModelDrafter(model=a.model or None, examples=g.entries) if use_model \
            else naming.RuleDrafter()
        judge = yesno.ModelJudge(model=a.model or None) if use_model else yesno.RuleJudge()
        history = [(s.client, s.rows) for s in usable]
        named, (rebuilt, reused, built) = naming.learn(history, g, drafter)
        judged = yesno.learn(history, book, judge)
        audience = gating.reseed(gating.load(store), gating.seed(usable))
        gl.save(g.entries, store)
        yesno.save(book, store)
        gating.save(audience, store)
        similar = sum(e.status == gl.SIMILAR for e in named) + sum(v.status == yesno.SIMILAR for v in judged)
        print(f"learn: {len(usable)} usable sheets; names: {len(named)} questions newly named, {rebuilt + reused + built} "
              f"acronyms filled or rebuilt; Yes/No: {len(judged)} questions judged; {similar} taken from a "
              f"confirmed rewording; audience: {len(audience)} questions")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    n, y = lr.write_review(g, book, a.out)
    print(f"learn: {n} name lines and {y} Yes/No lines wait for review -> {a.out}"
          + ("  (answer the Decision column, then: learn --review <file>)" if n or y else ""))


def cmd_refine(a):
    """Spec 01: a draft category list -> revised list -> final list."""
    if a.apply:
        flows.apply_file(a.apply, a.client, a.out, a.categories_out, a.version, _model(a))
        return
    if not a.draft:
        sys.exit("give a draft category list, or --apply <reviewed revised list>")
    target = tuple(int(x) for x in a.target.split("-")) if a.target else None
    flows.propose_file(a.draft, a.client, a.sheet, target, a.out, a.bank, a.research, a.country or "", _model(a))


def cmd_refine_evaluate(a):
    """Spec 01: compare a proposal with the list a team and client settled on."""
    from . import refine as rf
    from .privacy import register_clients
    register_clients([a.client])
    drafts = rf.read_draft(a.workbook, a.draft_tab)
    team = [d.description for d in rf.read_draft(a.workbook, a.final_tab)]
    use_model, why_not = _model_wanted(a)
    raw = rf.ModelRefiner(model=a.model or None).propose(drafts, a.client, past_categories(a.bank), None) \
        if use_model else None
    lines = rf.check(raw, drafts, rf.groups_of(drafts))
    if a.out:
        rf.write_revised(a.out, drafts, lines, a.client)
    print(f"refine-evaluate ({'model' if use_model else 'rules: ' + why_not}):")
    for k, v in rf.evaluate(drafts, lines, team).items():
        if isinstance(v, list):
            print(f"  {k}: {len(v)}")
            for x in v:
                print(f"      {x}")
        else:
            print(f"  {k}: {v}")


def _write_check_report(findings, path: str):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Findings"
    head = ["Sheet", "Row", "Rule", "Severity", "Class", "Problem", "Suggested fix"]
    ws.append(head)
    for f in findings:
        ws.append([f.sheet, f.excel_row, f.rule, f.severity, f.kind, f.message, f.suggestion])
    red = PatternFill("solid", fgColor="FFF4CCCC")
    for row in ws.iter_rows(min_row=2):
        if row[3].value == "error":
            row[3].fill = red
    for c, w in zip(ws[1], [20, 6, 20, 9, 9, 80, 60]):
        c.font = Font(bold=True)
        ws.column_dimensions[c.column_letter].width = w
    ws.freeze_panes = "A2"
    ws = wb.create_sheet("Summary")
    ws.append(["Sheet", "Errors", "Warnings"])
    per = defaultdict(Counter)
    for f in findings:
        per[f.sheet][f.severity] += 1
    for sheet, c in sorted(per.items()):
        ws.append([sheet, c["error"], c["warning"]])
    wb.save(path)


def cmd_check(a):
    """Spec 03: check a workbook after hand edits; never writes to it."""
    from . import check as ck
    cats = cat.load(a.categories) if a.categories else None
    yes_is = None
    book = yesno.load(a.yes_no)
    if book.verdicts:
        def yes_is(rows, i):
            v = book.lookup(trust.context_key(rows, i))
            return v.yes_is if v else None
    findings = ck.check_workbook(a.workbook, a.client, cats, a.against, yes_is)
    if not a.no_model and _model_wanted(a)[0]:
        from . import check_ai
        findings += check_ai.read(a.workbook, a.model or None, client=a.client)
    out = a.out or os.path.join(current().reports, os.path.splitext(os.path.basename(a.workbook))[0] + "_check.xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    _write_check_report(findings, out)
    sev = Counter(f.severity for f in findings)
    per = defaultdict(Counter)
    for f in findings:
        per[f.sheet][f.severity] += 1
    for sheet, c in sorted(per.items()):
        if c["error"]:
            print(f"  {sheet}: {c['error']} errors, {c['warning']} warnings")
    print(f"check: {sev['error']} errors, {sev['warning']} warnings in {len(per)} sheets "
          f"({', '.join(f'{r} {n}' for r, n in Counter(f.rule for f in findings if f.severity == 'error').most_common(8))})"
          f" -> {out}")
    if sev["error"]:
        sys.exit(1)


def _research_tools(a, sheets: List[Sheet]):
    """The research table, the bank's knowledge and (with a model) the web."""
    return research_tools(a.research, sheets, _model(a))


def cmd_research(a):
    """Spec 05: technical names, licences, questions, document names - bank first, then the web."""
    from .privacy import register_clients
    register_clients([a.client])
    table = rs.load(a.research)
    if a.review:
        applied, problems = rs.apply_review(a.review, table)
        rs.save(table, a.research)
        print(f"research: applied {applied} answered lines from {a.review}")
        for p in problems:
            print(f"REVIEW PROBLEM: {p}")
        return
    if not a.source_list:
        sys.exit("give a draft list or categories table, or --review <answered research review>")
    from . import refine as rf
    revised = None
    if a.source_list.endswith(".xlsx"):
        import openpyxl
        if {"Draft", "Revised"} <= set(openpyxl.load_workbook(a.source_list, read_only=True).sheetnames):
            revised = rf.read_revised(a.source_list)
    topics = [ln.description for ln in revised[1] if ln.status != rf.REPEATED] if revised else \
        [c.description for c in cat.load(a.source_list)] if a.source_list.endswith(".csv") \
        else [d.description for d in rf.read_draft(a.source_list, a.sheet)]
    country = a.country or rs.country_of(a.prefix)
    sheets = load_source(a)[0] if os.path.exists(a.bank) else []
    table, bank, web = _research_tools(a, sheets)
    kinds = [k.strip() for k in a.kinds.split(",") if k.strip()]
    new = rs.research(topics, country, kinds, table, bank, web, a.client)
    rs.save(table, a.research)
    if revised:
        rf.attach_research(revised[1], table, country)
        rf.write_revised(a.source_list, revised[0], revised[1], a.client, rs.read_target(a.source_list))
        print(f"research: research columns filled in {a.source_list} (its proposals unchanged)")
    out = a.out or os.path.join(current().reports, "research_review.xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    n = rs.write_review([f for f in table.findings if f.topic_key in {rs.topic_key(t) for t in topics}], out)
    by = Counter((f.kind, f.origin) for f in new)
    print(f"research: {len(topics)} topics, country {country or '-'}: {len(new)} new findings ("
          + ", ".join(f"{k} from the {o} {n_}" for (k, o), n_ in sorted(by.items())) + f"); {n} to review -> {out}")


def cmd_glossary(a):
    """Seed data/glossary.csv from the bank, keeping the team's confirmed and drafted rows."""
    sheets, _ = load_source(a)
    usable = trust.usable(sheets, trust.grade(sheets))
    fresh = gl.seed(usable)
    existing = gl.load(a.out) if os.path.exists(a.out) else []
    entries = gl.reseed(existing, fresh)
    if a.fill:
        use_model, why_not = _model_wanted(a)
        if not use_model:
            print(f"glossary: drafting by rules ({why_not})")
        drafter = naming.ModelDrafter(model=a.model or None, examples=entries) if use_model \
            else naming.RuleDrafter()
        rederived, reused, built = naming.complete_glossary(gl.Glossary(entries), drafter)
        print(f"glossary: {rederived} drafted acronyms rebuilt by the letter rule; empty acronyms: {reused} "
              f"taken from the same name elsewhere, {built} built by the rule; filled rows are drafts to confirm")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    gl.save(entries, a.out)
    kept = sum(e.status != gl.SEEDED for e in entries)
    full = sum(bool(e.short_name and e.acronym) for e in entries)
    clients = Counter(e.client for e in entries)
    print(f"glossary: {len(entries)} entries from {len(usable)} usable sheets "
          f"({', '.join(f'{c} {n}' for c, n in sorted(clients.items()))}); "
          f"{full} with both names, {len(entries) - full} with one; {kept} confirmed or drafted kept -> {a.out}")


def cmd_generate(a):
    _need(a.categories, "categories file", CATEGORIES_HOW)
    _need(a.review, "QA file to review",
          "run generate once without --review; it writes the QA file, which you then answer")
    _need(a.decisions, "decisions file", "it is reports/bank.xlsx, written by the ingest step")
    _need(a.marks, "marks file", "a CSV with columns code,section,marks")
    proposals = answers = None
    if a.review:
        from .review import read_answers, read_proposals
        proposals, answers = read_proposals(a.review), read_answers(a.review)
    _note_source(a)
    flows.generate(flows.GenerateSettings(
        a.categories, a.client, a.prefix, a.out, a.source, a.bank, a.client_name,
        [int(x) for x in a.only.split(",")] if a.only else None, a.decisions, a.marks, a.rulings, a.research,
        a.audience, a.yes_no, a.glossary, a.country, a.rebuild_existing, a.formulas, a.split_files,
        not a.no_ai_review, _model(a), proposals, answers))


def cmd_privacy_check(a):
    """Build every model request for a batch, anonymise it, scan it - send nothing."""
    from .privacy import Anonymiser
    from .selectors import SYSTEM, build_prompt
    _need(a.categories, "categories file", CATEGORIES_HOW)
    sheets, template_loader = load_source(a)
    cats = cat.load(a.categories)
    if a.only:
        wanted = {int(x) for x in a.only.split(",")}
        cats = [c for c in cats if c.number in wanted]
    gen = Generator(sheets, Retriever(sheets, groups_for(sheets, cats, a.client)), grades=trust.grade(sheets))
    out = os.path.join(current().outbox, "preview")
    os.makedirs(out, exist_ok=True)
    checked, leaking, replaced = 0, [], Counter()
    for c in cats:
        plan = gen.plan(Target(c.code(a.prefix), c.description, a.client, c.group, a.client_name,
                               c.sub_categories))
        if plan.same_category:
            continue
        anon = Anonymiser(a.client)
        raw = SYSTEM + "\n\n" + build_prompt(plan)
        sent = anon.anonymise(raw)
        replaced.update(t for t in re.findall(r"\[[A-Z]+(?:-[A-Z0-9]+)?\]", sent) if t != "[FLAGGED]")
        checked += 1
        if anon.leaks(sent):
            leaking.append((plan.target.code, anon.leaks(sent)))
        with open(os.path.join(out, f"{plan.target.code}.txt"), "w") as fh:
            fh.write(sent)
    if a.notice:
        from .category_llm import notice_text
        anon = Anonymiser(a.client)
        sent = anon.anonymise(notice_text(a.notice))
        replaced.update(t for t in re.findall(r"\[[A-Z]+(?:-[A-Z0-9]+)?\]", sent) if t != "[FLAGGED]")
        checked += 1
        if anon.leaks(sent):
            leaking.append(("notice", anon.leaks(sent)))
        with open(os.path.join(out, "notice.txt"), "w") as fh:
            fh.write(sent)
    print(f"checked {checked} requests; nothing was sent")
    print("replaced: " + ", ".join(f"{k} x{n}" for k, n in sorted(replaced.items(), key=lambda kv: -kv[1])))
    if leaking:
        print(f"WOULD BE REFUSED ({len(leaking)}):")
        for code, problems in leaking:
            print(f"  {code}: {', '.join(problems[:4])}")
    else:
        print("no identifying text left in any request")
    print(f"exact text that would be sent: {out}/")


def cmd_evaluate_model(a):
    from .evaluate import evaluate_model, model_jobs, summarise_model
    sheets, template_loader = load_source(a)
    groups = {}
    if a.categories:
        _need(a.categories, "categories file", CATEGORIES_HOW)
        groups = groups_for(sheets, cat.load(a.categories), a.client or "ERCS")
    gen = Generator(sheets, Retriever(sheets, groups), grades=trust.grade(sheets))
    jobs = model_jobs(gen, sheets, a.client)
    if a.limit:
        jobs = jobs[:a.limit]
    from .llm import make_llm
    llm = make_llm(model=a.model)          # fails here, before any cost, if the key or tier is missing
    from .selectors import PROMPT_VERSION
    cache = a.cache or os.path.join(current().cache, f"model_eval_{llm.model.replace('/', '_')}_v{PROMPT_VERSION}.json")
    import json
    cached = 0
    if os.path.exists(cache):
        with open(cache) as fh:
            cached = len(set(json.load(fh)) & {t.name for t, _ in jobs})
    reviewed = 0
    if os.path.exists(cache):
        with open(cache) as fh:
            reviewed = len({k[:-7] for k in json.load(fh) if k.endswith("#review")} & {t.name for t, _ in jobs})
    calls = (len(jobs) - cached) + (0 if a.no_review else len(jobs) - reviewed)
    print(f"{len(jobs)} rebuilt sheets to judge with {llm.provider}/{llm.model}"
          f"{'' if a.no_review else ' (proposer + reviewer)'}: {calls} model call(s) to make "
          f"(about ${calls * 0.03:.2f} at $2/$10 per million tokens)")
    print(f"every request is anonymised; exact text sent is logged in .cache/llm_outbox/")
    if a.dry_run:
        print("dry run: nothing sent")
        return
    from .selectors import ModelSelector
    reviewer = None
    if not a.no_review:
        from .reviewer import ModelReviewer
        reviewer = ModelReviewer()
    s, by_client, failures = evaluate_model(jobs, ModelSelector(llm=llm), cache, a.workers,
                                            reviewer=reviewer, gen=gen)
    print(summarise_model(s, by_client, failures))
    print(f"\nproposals saved in {cache} (delete it to start over)")


def cmd_evaluate(a):
    _need(a.categories, "categories file", CATEGORIES_HOW)
    sheets, template_loader = load_source(a)
    groups = {}
    if a.categories:
        groups = groups_for(sheets, cat.load(a.categories), a.client)
    gen = Generator(sheets, Retriever(sheets, groups))
    print(summarise(evaluate(gen, sheets, a.client)))


class _Print(logging.Handler):
    """The flows' report lines, printed as they come (to the stdout of the moment, so tests can capture it)."""

    def emit(self, record):
        print(record.getMessage())


def main(argv=None):
    P = current()
    ap = argparse.ArgumentParser(prog="tendersure", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="validate a corpus and build the bank")
    p.add_argument("corpus", help="folder of criteria workbooks (subfolders included)")
    p.add_argument("--bank", default=P.bank, help="the bank to add them to")
    p.add_argument("--out", default=P.reports)
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("categories", help="extract categories from a notice PDF")
    p.add_argument("pdf")
    p.add_argument("--client", required=True)
    p.add_argument("--version", help="label for this revision of the notice, e.g. 2026-09-ext")
    p.add_argument("--out", default="categories.csv", help=".csv or .json")
    p.add_argument("--diff", help="earlier extraction to compare against")
    p.add_argument("--engine", choices=["layout", "llm", "both"], default="layout",
                   help="layout: table geometry, no network (fits the ERCS notice); "
                        "llm: a model (any OpenRouter id) reads the PDF, code verifies every word against it; "
                        "both: run both and reconcile")
    p.add_argument("--model", help="OpenRouter model id for --engine llm/both, e.g. "
                   "google/gemini-4-argon (default: $TENDERSURE_MODEL)")
    p.set_defaults(fn=cmd_categories)

    p = sub.add_parser("refine", help="propose a revised category list from a draft; or apply its review")
    p.add_argument("draft", nargs="?", help="the draft category list (.xlsx or .csv)")
    p.add_argument("--client", required=True, help="client code, e.g. ERCS")
    p.add_argument("--sheet", help="the tab holding the draft (default: the first)")
    p.add_argument("--target", help="category count to aim at, e.g. 50-60 (reported against, never forced)")
    p.add_argument("--apply", help="a revised list with its Decision column answered: write the final list")
    p.add_argument("--categories-out", help="with --apply: write the categories table for generate here")
    p.add_argument("--version", help="with --apply: label for this version of the list")
    p.add_argument("--out", help="the workbook to write")
    p.add_argument("--bank", default=P.bank, help="past exercises, for suggested additions")
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="propose by rules only")
    p.add_argument("--country", help="ISO country code for researched licences, e.g. ET, KE")
    p.add_argument("--research", default=P.learned("research"), help="the research table (default data/)")
    p.set_defaults(fn=cmd_refine)

    p = sub.add_parser("refine-evaluate", help="compare a refinement with the list a team settled on")
    p.add_argument("workbook", help="a workbook with the team's draft and final category tabs")
    p.add_argument("--draft-tab", required=True)
    p.add_argument("--final-tab", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--out", help="also write the proposal as a revised list")
    p.add_argument("--bank", default=P.bank)
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true")
    p.set_defaults(fn=cmd_refine_evaluate)

    p = sub.add_parser("check", help="check a workbook after hand edits (never changes it; exit 1 on errors)")
    p.add_argument("workbook")
    p.add_argument("--client", help="client code, e.g. ERCS: codes must carry it, other clients' names are flagged")
    p.add_argument("--categories", help="the categories table: each sheet is checked against its category")
    p.add_argument("--against", help="the generated workbook: what changed since is listed")
    p.add_argument("--yes-no", default=P.learned("yesno_polarity"), help="Yes/No verdicts (default data/)")
    p.add_argument("--out", help="the report workbook (default reports/<name>_check.xlsx)")
    p.add_argument("--model", nargs="?", const="", help="model id for the AI read (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="skip the AI read")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("research", help="research technical names, licences, questions: bank first, then the web")
    p.add_argument("source_list", nargs="?", help="a draft list (.xlsx) or a categories table (.csv)")
    p.add_argument("--client", required=True)
    p.add_argument("--sheet", help="the draft list's tab")
    p.add_argument("--country", help="ISO country code, e.g. ET, KE (default: from --prefix)")
    p.add_argument("--prefix", help="sheet code prefix, e.g. ERCS-ET-2026- (gives the country)")
    p.add_argument("--kinds", default="names,licences", help="names, licences, questions, documents")
    p.add_argument("--research", default=P.learned("research"), help="the research table (default data/)")
    p.add_argument("--review", help="an answered research review to apply")
    p.add_argument("--out", help="the review workbook (default reports/research_review.xlsx)")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="our own data only, no web search")
    p.set_defaults(fn=cmd_research, source=None)

    p = sub.add_parser("learn", help="learn names and Yes/No verdicts for the whole bank; write the review")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--store", default=P.data, help="folder of the learned files (default data/)")
    p.add_argument("--out", default=os.path.join(P.reports, "learned_review.xlsx"), help="the review workbook")
    p.add_argument("--review", help="an answered review workbook to apply")
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="draft by rules, without a model")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("glossary", help="seed the short name / acronym glossary from the bank")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--out", default=P.learned("glossary"))
    p.add_argument("--fill", action="store_true",
                   help="give every entry both names: drafts' acronyms by the letter rule, empty ones reused or built")
    p.add_argument("--model", nargs="?", const="", help="model id for --fill (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="--fill by rules, without a model")
    p.set_defaults(fn=cmd_glossary)

    p = sub.add_parser("generate", help="build criteria sheets for categories")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--categories", required=True, help="output of the categories command")
    p.add_argument("--client", required=True, help="client code, e.g. ERCS")
    p.add_argument("--client-name", help="name used inside question text, if not the code")
    p.add_argument("--prefix", required=True, help="sheet code prefix, e.g. ERCS-ET-2026-")
    p.add_argument("--only", help="comma-separated category numbers")
    p.add_argument("--decisions", help="bank.xlsx with the Decision column filled in")
    p.add_argument("--marks", help="CSV of code,section,marks to override the suggested split")
    p.add_argument("--review", help="QA report whose Decisions tab has been filled in; answers are applied")
    p.add_argument("--rulings", default=P.learned("rulings"),
                   help="standing rulings: read every run, extended by C- answers (default data/rulings.csv)")
    p.add_argument("--rebuild-existing", action="store_true",
                   help="ignore an existing sheet with the same code instead of reusing it")
    p.add_argument("--formulas", action="store_true", help="write =SUM() subtotals instead of values")
    p.add_argument("--split-files", action="store_true", help="one workbook per category")
    p.add_argument("--no-ai-review", action="store_true",
                   help="with --model: stop after the proposals (rewordings applied) and leave the rest to a person")
    p.add_argument("--model", nargs="?", const="",
                   help="model id to use (default: TENDERSURE_MODEL). The AI runs automatically whenever "
                        "a model is configured in .env")
    p.add_argument("--no-model", action="store_true", help="build without any AI step")
    p.add_argument("--country", help="ISO country code for research (default: from --prefix, e.g. ERCS-ET-...)")
    p.add_argument("--research", default=P.learned("research"), help="the research table (default data/)")
    p.add_argument("--audience", default=P.learned("audience"),
                   help="which business types answer each question (default data/audience.csv)")
    p.add_argument("--yes-no", default=P.learned("yesno_polarity"),
                   help="whether Yes is the good answer, per Yes/No question (default data/yesno_polarity.csv)")
    p.add_argument("--glossary", default=P.learned("glossary"),
                   help="short names and acronyms (default data/glossary.csv; seeded from the bank if absent)")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("privacy-check", help="show what model calls would send (anonymised); sends nothing")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--categories", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--client-name")
    p.add_argument("--prefix", required=True)
    p.add_argument("--only")
    p.add_argument("--notice", help="also check the notice PDF's anonymised text")
    p.set_defaults(fn=cmd_privacy_check)

    p = sub.add_parser("evaluate-model",
                       help="score the model's judgement against sheets the team built (costs model calls)")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--client", help="only this client's sheets")
    p.add_argument("--categories", help="categories file, to use the notice grouping")
    p.add_argument("--model", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--limit", type=int, help="judge only the first N sheets (a cheap first look)")
    p.add_argument("--workers", type=int, default=3, help="calls in parallel (default 3)")
    p.add_argument("--cache", help="where proposals are saved for resuming")
    p.add_argument("--dry-run", action="store_true", help="show how many calls it would make; send nothing")
    p.add_argument("--no-review", action="store_true", help="score the proposer alone, without the AI reviewer")
    p.set_defaults(fn=cmd_evaluate_model)

    p = sub.add_parser("evaluate", help="leave-one-out against existing sheets")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default=P.bank, help=argparse.SUPPRESS)
    p.add_argument("--client")
    p.add_argument("--categories", help="categories file for --client, to use the advert grouping")
    p.set_defaults(fn=cmd_evaluate)

    a = ap.parse_args(argv)
    handler = _Print()
    flows.log.addHandler(handler)
    flows.log.setLevel(logging.INFO)
    flows.log.propagate = False
    try:
        a.fn(a)
    except (LLMError, PrivacyError) as e:
        sys.exit(f"model step stopped: {e}")
    except flows.FlowError as e:
        sys.exit(str(e))
    finally:
        flows.log.removeHandler(handler)


if __name__ == "__main__":
    main()
