"""Command line: python -m tendersure <command> ...

  ingest      add a folder of workbooks to the bank (data/bank.sqlite; only
              new or changed files are read) and write the reports
  categories  extract the category table from a prequalification notice PDF
  generate    build criteria sheets from the bank for a notice's categories,
              rewording copied questions (with a model), plus a QA report
  evaluate    leave-one-out check against the sheets the team actually built
"""

import argparse
import csv
import glob
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, List

from . import categories as cat
from . import glossary as gl
from . import naming
from . import yesno
from . import category_llm as cl
from .bank import Bank
from .evaluate import evaluate, summarise
from .llm import LLMError
from .privacy import PrivacyError
from .generate import Generator, MarksError, Target, apply_section_marks, check_total, finalise
from .parser import Sheet, parse_workbook
from .reports import load_decisions, write_bank, write_qa_report, write_validation_report
from .similarity import Retriever
from .validate import check_corpus, validate_sheet
from . import trust
from .writer import Writer


CACHE_DIR = ".cache"


def load_corpus(folder: str, cache: bool = None) -> List[Sheet]:
    """Every workbook under `folder` (subfolders included, e.g. one per year).

    Parsed sheets are cached per file and reused until the file changes, so a
    corpus of years only costs a full parse once.
    """
    if cache is None:
        cache = os.environ.get("TENDERSURE_NO_CACHE") != "1"
    files = sorted(f for f in glob.glob(os.path.join(folder, "**", "*.xlsx"), recursive=True)
                   if not os.path.basename(f).startswith("~$"))
    if not os.path.isdir(folder):
        sys.exit(f"corpus folder not found: {folder}\n  quote the path if it contains spaces")
    if not files:
        sys.exit(f"no .xlsx files in {folder}")
    sheets = []
    for f in files:
        sheets.extend(_parse_cached(f) if cache else parse_workbook(f))
    from .privacy import register_clients
    register_clients(s.client for s in sheets)     # every client is anonymised in model calls
    names = Counter(s.name for s in sheets)
    for s in sheets:                       # the same tab name in two workbooks must stay distinct
        if names[s.name] > 1:
            s.name = f"{s.name} [{os.path.splitext(s.source_file)[0]}]"
    return sheets


def load_source(a):
    """Sheets for a command: from the bank (default), or straight from a folder.

    Returns (sheets, template_loader for the writer)."""
    from .store import BankStore
    src = getattr(a, "source", None)
    if src and os.path.isdir(src):
        print(f"note: reading workbooks straight from '{src}'; run `ingest` to put them in the bank",
              file=sys.stderr)
        return load_corpus(src), None
    path = src or a.bank
    if not os.path.exists(path):
        sys.exit(f"no bank at {path}\n  build it first: python -m tendersure ingest \"<folder of workbooks>\"")
    store = BankStore(path)
    sheets = store.load_sheets()
    if not sheets:
        sys.exit(f"the bank at {path} is empty; run ingest first")
    return sheets, store.template


def _parse_cached(path: str) -> List[Sheet]:
    import hashlib
    import pickle
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    cached = os.path.join(CACHE_DIR, key + ".pkl")
    if os.path.exists(cached):
        try:
            with open(cached, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass
    sheets = parse_workbook(path)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cached, "wb") as fh:
        pickle.dump(sheets, fh)
    return sheets


def _groups_for(sheets: List[Sheet], cats: List[cat.Category], client: str) -> Dict[str, str]:
    """Advert group per past sheet, via the category number at the end of its code."""
    by_num = {c.number: c.group for c in cats}
    out = {}
    for s in sheets:
        if s.client != client or not s.code:
            continue
        digits = "".join(ch for ch in s.code.split("-")[-1].split("_")[-1] if ch.isdigit())
        if digits and int(digits) in by_num:
            out[s.name] = by_num[int(digits)]
    return out


def cmd_ingest(a):
    from .store import BankStore
    if not os.path.isdir(a.corpus):
        sys.exit(f"folder not found: {a.corpus}\n  quote the path if it contains spaces")
    store = BankStore(a.bank)
    st = store.ingest_folder(a.corpus)
    print(f"bank {a.bank}: {st['added']} workbook(s) added ({st['sheets_added']} sheets), "
          f"{st['unchanged']} already in the bank, {st['replaced']} replaced by a newer version")
    sheets = store.load_sheets()
    findings = [f for s in sheets for f in validate_sheet(s)] + check_corpus(sheets)
    bank = Bank()
    bank.ingest(sheets)
    bank.finalise()
    os.makedirs(a.out, exist_ok=True)
    grades = trust.grade(sheets)
    vr = write_validation_report(sheets, findings, os.path.join(a.out, "validation_report.xlsx"), grades)
    br = write_bank(bank, os.path.join(a.out, "bank.xlsx"))
    t = bank.tiers()
    kinds = Counter(f.kind for f in findings)
    print(f"{len(sheets)} sheets, {sum(len(s.rows) for s in sheets)} questions")
    print(f"{len(findings)} findings ({kinds['platform']} platform, {kinds['silent']} silent)")
    tiers = Counter(g.tier for g in grades.values())
    print(f"sheets graded automatically: {tiers['clean']} clean, {tiers['correctable']} fixed on reuse, "
          f"{tiers['quarantined']} quarantined (never copied), {tiers['superseded']} superseded - no review needed")
    print(f"bank: {len(bank.entries)} questions (core {len(t['core'])}, common {len(t['common'])}, "
          f"category {len(t['category'])}); {len(bank.conflicts())} cross-sheet disagreements (reference only: "
          f"settled by client convention, or asked in a batch that uses them)")
    print(f"wrote {vr}\nwrote {br}")


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


def _read_marks(path: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = defaultdict(dict)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["code"].strip()][row["section"].strip()] = float(row["marks"])
    return out


def _need(path: str, what: str, how: str):
    if path and not os.path.exists(path):
        sys.exit(f"{what} not found: {path}\n  {how}")


CATEGORIES_HOW = ('create it first with the categories step, e.g.\n'
                  '  python -m tendersure categories "<notice.pdf>" --client ERCS --out data/ercs_categories.csv')


def _model_wanted(a):
    """The AI runs by default whenever a model is configured; --no-model turns it off."""
    if getattr(a, "no_model", False):
        return False, "--no-model given"
    if a.model is not None:
        return True, ""
    from .llm import load_dotenv
    load_dotenv()
    provider = os.environ.get("TENDERSURE_PROVIDER", "openrouter").lower()
    if provider == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            return False, "no GEMINI_API_KEY in .env"
        if os.environ.get("GEMINI_PAID_TIER", "").lower() != "yes":
            return False, "GEMINI_PAID_TIER is not 'yes' (unpaid keys may expose prompts)"
        return True, ""
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False, "no model key in .env (see .env.example)"
    return True, ""


def _glossary(path: str, sheets: List[Sheet]) -> "gl.Glossary":
    """The team's glossary; seeded from the bank the first time."""
    if os.path.exists(path):
        return gl.Glossary(gl.load(path))
    entries = gl.seed(trust.usable(sheets, trust.grade(sheets)))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    gl.save(entries, path)
    print(f"glossary: seeded {len(entries)} entries from the bank -> {path}")
    return gl.Glossary(entries)


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
        gl.save(g.entries, store)
        yesno.save(book, store)
        similar = sum(e.status == gl.SIMILAR for e in named) + sum(v.status == yesno.SIMILAR for v in judged)
        print(f"learn: {len(usable)} usable sheets; names: {len(named)} questions newly named, {rebuilt + reused + built} "
              f"acronyms filled or rebuilt; Yes/No: {len(judged)} questions judged; {similar} taken from a "
              f"confirmed rewording")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    n, y = lr.write_review(g, book, a.out)
    print(f"learn: {n} name lines and {y} Yes/No lines wait for review -> {a.out}"
          + ("  (answer the Decision column, then: learn --review <file>)" if n or y else ""))


def _past_categories(bank: str) -> List[str]:
    """Category descriptions from past exercises, for suggested additions."""
    if not os.path.exists(bank):
        return []
    from .store import BankStore
    seen = {}
    for s in BankStore(bank).load_sheets():
        if s.description:
            seen.setdefault(" ".join(s.description.lower().split()), s.description.strip())
    return sorted(seen.values())


def cmd_refine(a):
    """Spec 01: a draft category list -> revised list -> final list."""
    from . import refine as rf
    from .privacy import register_clients
    register_clients([a.client])
    use_model, why_not = _model_wanted(a)
    if a.apply:
        drafts, lines = rf.read_revised(a.apply)
        notes = [(ln, ln.comment) for ln in lines if ln.comment and ln.decision != "edit"]
        final, excluded, problems = rf.apply_review(drafts, lines)
        changes = rf.ModelRefiner(model=a.model or None).interpret(notes, a.client) if use_model and notes \
            else ["" for _ in notes]
        out = a.out or os.path.splitext(a.apply)[0] + "_final.xlsx"
        rf.write_final(out, final, [(ln, c, ch) for (ln, c), ch in zip(notes, changes)])
        print(f"refine: {len(drafts)} draft rows -> {len(final)} final categories "
              f"({len(excluded)} lines removed, rejected or folded); {len(notes)} client comments listed -> {out}")
        if a.categories_out:
            cat.save(rf.to_categories(final, a.client, a.version), a.categories_out)
            print(f"refine: categories table -> {a.categories_out} (the input of generate)")
        for p in problems:
            print(f"REVIEW PROBLEM: {p}")
        return
    if not a.draft:
        sys.exit("give a draft category list, or --apply <reviewed revised list>")
    drafts = rf.read_draft(a.draft, a.sheet)
    target = tuple(int(x) for x in a.target.split("-")) if a.target else None
    if not use_model:
        print(f"refine: without a model ({why_not}): duplicates folded, overlaps raised as questions")
    raw = rf.ModelRefiner(model=a.model or None).propose(drafts, a.client, _past_categories(a.bank), target) \
        if use_model else None
    lines = rf.check(raw, drafts, rf.groups_of(drafts))
    out = a.out or os.path.join("reports", os.path.splitext(os.path.basename(a.draft))[0] + " - Revised.xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    rf.write_revised(out, drafts, lines, a.client, target)
    kept = sum(ln.status != rf.REPEATED for ln in lines)
    counts = Counter(ln.status for ln in lines)
    print(f"refine: {len(drafts)} draft rows -> {kept} proposed categories"
          + (f" (target {target[0]}-{target[1]})" if target else "") + "; "
          + ", ".join(f"{n} {s}" for s, n in counts.most_common())
          + f"; {sum(bool(ln.question) for ln in lines)} questions for the client -> {out}")


def cmd_refine_evaluate(a):
    """Spec 01: compare a proposal with the list a team and client settled on."""
    from . import refine as rf
    from .privacy import register_clients
    register_clients([a.client])
    drafts = rf.read_draft(a.workbook, a.draft_tab)
    team = [d.description for d in rf.read_draft(a.workbook, a.final_tab)]
    use_model, why_not = _model_wanted(a)
    raw = rf.ModelRefiner(model=a.model or None).propose(drafts, a.client, _past_categories(a.bank), None) \
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
    sheets, template_loader = load_source(a)
    cats = cat.load(a.categories)
    if a.only:
        wanted = {int(x) for x in a.only.split(",")}
        cats = [c for c in cats if c.number in wanted]
    groups = _groups_for(sheets, cat.load(a.categories), a.client)
    decisions = load_decisions(a.decisions) if a.decisions else {}
    marks = _read_marks(a.marks) if a.marks else {}
    grades = trust.grade(sheets)
    rulings, ruled = trust.load_rulings(a.rulings)
    gen = Generator(sheets, Retriever(sheets, groups), decisions, grades, rulings)

    plans, failed = [], []
    for c in cats:
        code = c.code(a.prefix)
        exclude = {s.name for s in sheets if s.code == code} if a.rebuild_existing else None
        target = Target(code, c.description, a.client, c.group, a.client_name, c.sub_categories,
                        c.requirements, c.eligibility, c.gate_question, c.applies_to)
        try:
            plan = gen.plan(target, exclude=exclude, marks=marks.get(code))
            check_total(plan)
        except MarksError as e:
            failed.append((code, str(e)))
            continue
        plans.append(plan)

    # Model proposals: replayed from the review file when applying a review
    # (exactly what the reviewer saw, no new model call); otherwise asked for.
    if a.review:
        from .review import read_proposals
        stored = read_proposals(a.review)
        for p in plans:
            p.proposal = stored.get(p.target.code)
    use_model, why_not = _model_wanted(a)
    if not use_model:
        print(f"building without AI: {why_not}")
    ai_cache, save_ai_cache = {}, None
    if use_model:
        import hashlib
        import json
        from .selectors import PROMPT_VERSION, ModelSelector, build_prompt
        selector = ModelSelector(model=a.model or None)
        # Every AI answer is saved the moment it arrives: a rerun after a crash
        # (or with unchanged inputs) reuses it instead of paying again.
        cache_path = os.path.join(".cache", f"build_{a.client}_{selector.llm.model.replace('/', '_')}"
                                            f"_v{PROMPT_VERSION}.json")
        if os.path.exists(cache_path):
            with open(cache_path) as fh:
                ai_cache = json.load(fh)

        def save_ai_cache():
            os.makedirs(".cache", exist_ok=True)
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
                print(f"{p.target.code}: no model proposal ({e})")
                continue
            ai_cache[key] = json.loads(json.dumps(prop))
            save_ai_cache()
            asked += 1
            print(f"{p.target.code}: {prop['model']} rewords {len(prop.get('reword', {}))} (applied), "
                  f"proposes {len(prop['drop'])} removal(s), "
                  f"{len(prop['add'])} addition(s), {len(prop['drafts'])} new question(s)"
                  + (f"; ignored {len(prop['ignored'])} invalid" if prop["ignored"] else ""))
        print(f"model proposals: {asked} new, {reused} reused from earlier runs")

    items = None
    if use_model and not a.review:
        if a.no_ai_review:
            from .review import auto_reword
            items, n = auto_reword(plans)
            if n:
                print(f"reworded {n} copied question(s); removals and additions wait in the QA file")
        else:
            from .reviewer import ModelReviewer, ai_review
            print("AI review: a second pass checks every proposal; only what both agree on is applied")
            items, summary = ai_review(gen, plans, ModelReviewer(), cache=ai_cache, save=save_ai_cache)
            print(f"AI review: {summary['answered']} decisions answered, {summary['disagreements']} "
                  f"disagreement(s) kept as the client's past sheet had it, "
                  f"{len(summary['rebalanced'])} sheet(s) rebalanced to 100 (all recorded in the QA file)")
            new_rulings = [i.ruling for i in items if i.ruling]
            if new_rulings:
                trust.save_rulings(a.rulings, new_rulings)
                print(f"saved {len(new_rulings)} client convention(s) settled by majority to {a.rulings}")
    if a.review:
        from .review import apply_answers, read_answers
        items, log, problems = apply_answers(plans, read_answers(a.review), ruled)
        new_rulings = [i.ruling for i in items if i.ruling]
        if new_rulings:
            trust.save_rulings(a.rulings, new_rulings)
            print(f"saved {len(new_rulings)} standing ruling(s) to {a.rulings}")
        for line in log:
            print(f"review: {line}")
        for line in problems:
            print(f"REVIEW PROBLEM: {line}")

    for p in plans:
        finalise(p)
    book = yesno.load(a.yes_no)
    judge = yesno.ModelJudge(model=a.model or None) if use_model else yesno.RuleJudge()
    judged = yesno.settle(plans, book, judge)
    if judged:
        yesno.save(book, a.yes_no)
        bad = sum(v.yes_is == "bad" for v in judged)
        print(f"yes/no: judged {len(judged)} scored questions ({bad} where Yes is bad; "
              f"{sum(v.status == yesno.RULE_DRAFTED for v in judged)} by keywords), saved as drafts in "
              f"{a.yes_no}: confirm or change them there")
    glossary = _glossary(a.glossary, sheets)
    drafter = naming.ModelDrafter(model=a.model or None, examples=glossary.entries) if use_model \
        else naming.RuleDrafter()
    drafted = naming.name_documents(plans, glossary, drafter)
    from . import learned_review as lr
    n, y = lr.write_review(glossary, book, os.path.splitext(a.out)[0] + "_learned_review.xlsx")
    if n or y:
        print(f"learned drafts waiting for review: {n} name lines, {y} Yes/No lines -> "
              f"{os.path.splitext(a.out)[0]}_learned_review.xlsx (answer it, then: learn --review <file>)")
    if drafted:
        gl.save(glossary.entries, a.glossary)
        by_rules = sum(e.status == gl.RULE_DRAFTED for e in drafted)
        print(f"glossary: named {len(drafted)} new documents ({by_rules} short names drafted by rules, "
              f"the rest given or drafted by the model; acronyms by the letter rule), saved as drafts in "
              f"{a.glossary}: confirm or change them there")

    # Whatever happened above, a sheet that does not total 100 is listed, not
    # written - and never stops the rest of the batch.
    for p in list(plans):
        try:
            check_total(p)
        except MarksError as e:
            plans.remove(p)
            failed.append((p.target.code, str(e)))

    if not plans:
        sys.exit("nothing to write: " + "; ".join(m for _, m in failed))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    writer = Writer(formulas=a.formulas, template_loader=template_loader)
    if a.split_files:
        base = os.path.splitext(a.out)[0]
        for p in plans:
            writer.write([p], f"{base}_{p.target.code}.xlsx")
        print(f"wrote {len(plans)} files {base}_<code>.xlsx")
    else:
        writer.write(plans, a.out)
        print(f"wrote {len(plans)} sheets -> {a.out}")
    from .review import decision_items
    items = items if items is not None else decision_items(plans)
    qa = write_qa_report(plans, os.path.splitext(a.out)[0] + "_qa.xlsx", items)
    open_items = [i for i in items if not i.answer and not i.status]
    kinds = Counter(i.id[0] for i in open_items)
    print(f"wrote {qa}")
    print(f"Decisions tab: {len(items)} decisions, {len(items) - len(open_items)} settled; open: "
          f"{kinds['Q']} questions, {kinds['A']} additions, {kinds['M']} marks, "
          f"{kinds['C']} client conventions to settle, {kinds['N']} categories with no source, "
          f"{kinds['T'] + kinds['F']} team-wide")
    for code, msg in failed:
        print(f"NOT WRITTEN {code}: {msg}")


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
    gen = Generator(sheets, Retriever(sheets, _groups_for(sheets, cats, a.client)), grades=trust.grade(sheets))
    out = os.path.join(".cache", "llm_outbox", "preview")
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
        groups = _groups_for(sheets, cat.load(a.categories), a.client or "ERCS")
    gen = Generator(sheets, Retriever(sheets, groups), grades=trust.grade(sheets))
    jobs = model_jobs(gen, sheets, a.client)
    if a.limit:
        jobs = jobs[:a.limit]
    from .llm import make_llm
    llm = make_llm(model=a.model)          # fails here, before any cost, if the key or tier is missing
    from .selectors import PROMPT_VERSION
    cache = a.cache or os.path.join(".cache", f"model_eval_{llm.model.replace('/', '_')}_v{PROMPT_VERSION}.json")
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
        groups = _groups_for(sheets, cat.load(a.categories), a.client)
    gen = Generator(sheets, Retriever(sheets, groups))
    print(summarise(evaluate(gen, sheets, a.client)))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tendersure", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="validate a corpus and build the bank")
    p.add_argument("corpus", help="folder of criteria workbooks (subfolders included)")
    p.add_argument("--bank", default="data/bank.sqlite", help="the bank to add them to")
    p.add_argument("--out", default="reports")
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
    p.add_argument("--bank", default="data/bank.sqlite", help="past exercises, for suggested additions")
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="propose by rules only")
    p.set_defaults(fn=cmd_refine)

    p = sub.add_parser("refine-evaluate", help="compare a refinement with the list a team settled on")
    p.add_argument("workbook", help="a workbook with the team's draft and final category tabs")
    p.add_argument("--draft-tab", required=True)
    p.add_argument("--final-tab", required=True)
    p.add_argument("--client", required=True)
    p.add_argument("--out", help="also write the proposal as a revised list")
    p.add_argument("--bank", default="data/bank.sqlite")
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true")
    p.set_defaults(fn=cmd_refine_evaluate)

    p = sub.add_parser("learn", help="learn names and Yes/No verdicts for the whole bank; write the review")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default="data/bank.sqlite", help=argparse.SUPPRESS)
    p.add_argument("--store", default="data", help="folder of the learned files (default data/)")
    p.add_argument("--out", default="reports/learned_review.xlsx", help="the review workbook")
    p.add_argument("--review", help="an answered review workbook to apply")
    p.add_argument("--model", nargs="?", const="", help="model id (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="draft by rules, without a model")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("glossary", help="seed the short name / acronym glossary from the bank")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default="data/bank.sqlite", help=argparse.SUPPRESS)
    p.add_argument("--out", default="data/glossary.csv")
    p.add_argument("--fill", action="store_true",
                   help="give every entry both names: drafts' acronyms by the letter rule, empty ones reused or built")
    p.add_argument("--model", nargs="?", const="", help="model id for --fill (default: TENDERSURE_MODEL)")
    p.add_argument("--no-model", action="store_true", help="--fill by rules, without a model")
    p.set_defaults(fn=cmd_glossary)

    p = sub.add_parser("generate", help="build criteria sheets for categories")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default="data/bank.sqlite", help=argparse.SUPPRESS)
    p.add_argument("--categories", required=True, help="output of the categories command")
    p.add_argument("--client", required=True, help="client code, e.g. ERCS")
    p.add_argument("--client-name", help="name used inside question text, if not the code")
    p.add_argument("--prefix", required=True, help="sheet code prefix, e.g. ERCS-ET-2026-")
    p.add_argument("--only", help="comma-separated category numbers")
    p.add_argument("--decisions", help="bank.xlsx with the Decision column filled in")
    p.add_argument("--marks", help="CSV of code,section,marks to override the suggested split")
    p.add_argument("--review", help="QA report whose Decisions tab has been filled in; answers are applied")
    p.add_argument("--rulings", default="data/rulings.csv",
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
    p.add_argument("--yes-no", default="data/yesno_polarity.csv",
                   help="whether Yes is the good answer, per Yes/No question (default data/yesno_polarity.csv)")
    p.add_argument("--glossary", default="data/glossary.csv",
                   help="short names and acronyms (default data/glossary.csv; seeded from the bank if absent)")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("privacy-check", help="show what model calls would send (anonymised); sends nothing")
    p.add_argument("source", nargs="?", help="the bank (default data/bank.sqlite); a folder of workbooks also works")
    p.add_argument("--bank", default="data/bank.sqlite", help=argparse.SUPPRESS)
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
    p.add_argument("--bank", default="data/bank.sqlite", help=argparse.SUPPRESS)
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
    p.add_argument("--bank", default="data/bank.sqlite", help=argparse.SUPPRESS)
    p.add_argument("--client")
    p.add_argument("--categories", help="categories file for --client, to use the advert grouping")
    p.set_defaults(fn=cmd_evaluate)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (LLMError, PrivacyError) as e:
        sys.exit(f"model step stopped: {e}")


if __name__ == "__main__":
    main()
