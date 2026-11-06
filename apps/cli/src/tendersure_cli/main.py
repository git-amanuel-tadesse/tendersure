"""Command line: tendersure <command> ...

  ingest      add a folder of workbooks to the bank (data/bank.sqlite; only
              new or changed files are read) and write the reports
  categories  extract the category table from a prequalification notice PDF
  refine      propose a revised category list from a draft; or apply its review
  research    technical names, licences, questions: the bank first, then the web
  learn       names and Yes/No verdicts for the whole bank, and their review
  generate    build criteria sheets from the bank for a notice's categories,
              rewording copied questions (with a model), plus a QA report
  check       check a workbook after hand edits (exit 1 on errors)
  evaluate    leave-one-out check against the sheets the team actually built

Arguments in, flows out: every step is a tendersure.flows function; this
module only parses arguments, prints the flows' report lines and sets the
exit code.
"""

import argparse
import logging
import os
import sys

from tendersure import flows


def _note_source(a):
    src = getattr(a, "source", None)
    if src and os.path.isdir(src):
        print(f"note: reading workbooks straight from '{src}'; run `ingest` to put them in the bank",
              file=sys.stderr)


def _need(path: str, what: str, how: str):
    if path and not os.path.exists(path):
        sys.exit(f"{what} not found: {path}\n  {how}")


CATEGORIES_HOW = ('create it first with the categories step, e.g.\n'
                  '  tendersure categories "<notice.pdf>" --client ERCS --out data/ercs_categories.csv')


def _model(a) -> flows.Model:
    """--model [id] chooses a model (bare: the configured default); --no-model turns every model step off."""
    return flows.Model(a.model, getattr(a, "no_model", False))


def _only(a):
    return [int(x) for x in a.only.split(",")] if a.only else None


def cmd_ingest(a):
    flows.ingest(a.corpus, a.bank, a.out)


def cmd_categories(a):
    _need(a.pdf, "notice PDF", "check the path; quote it if it contains spaces")
    flows.extract_categories(a.pdf, a.client, a.out, a.version, a.engine, a.diff, flows.Model(a.model))


def cmd_refine(a):
    """Spec 01: a draft category list -> revised list -> final list."""
    if a.apply:
        flows.apply_file(a.apply, a.client, a.out, a.categories_out, a.version, _model(a))
        return
    if not a.draft:
        sys.exit("give a draft category list, or --apply <reviewed revised list>")
    target = tuple(int(x) for x in a.target.split("-")) if a.target else None
    previous = None
    if a.previous:
        _need(a.previous, "previous category list", "check the path; quote it if it contains spaces")
        previous = flows.read_previous(a.previous, a.previous_sheet, a.previous_label)
    flows.propose_file(a.draft, a.client, a.sheet, target, a.out, a.bank, a.research, a.country or "", _model(a),
                       flows.load_brief(a.brief), previous)


def cmd_refine_evaluate(a):
    flows.evaluate_refinement(a.workbook, a.draft_tab, a.final_tab, a.client, a.out, a.bank, _model(a))


def cmd_research(a):
    """Spec 05: technical names, licences, questions, document names - bank first, then the web."""
    if a.review:
        flows.apply_research_review(a.review, a.client, a.research)
        return
    kinds = [k.strip() for k in a.kinds.split(",") if k.strip()]
    flows.research(a.source_list, a.client, kinds, a.prefix, a.country, a.sheet, a.out, a.bank, a.research,
                   _model(a))


def cmd_learn(a):
    """Spec 04: learn names and Yes/No verdicts for every question in the bank."""
    if a.review:
        flows.apply_learned_review(a.review, a.store, a.out)
        return
    _note_source(a)
    flows.learn(a.store, a.out, a.source, a.bank, _model(a))


def cmd_glossary(a):
    _note_source(a)
    flows.seed_glossary(a.out, a.source, a.bank, a.fill, _model(a))


def cmd_generate(a):
    _need(a.categories, "categories file", CATEGORIES_HOW)
    _need(a.review, "QA file to review",
          "run generate once without --review; it writes the QA file, which you then answer")
    _need(a.decisions, "decisions file", "it is reports/bank.xlsx, written by the ingest step")
    _need(a.marks, "marks file", "a CSV with columns code,section,marks")
    proposals = answers = None
    if a.review:
        proposals, answers = flows.read_review(a.review)
    _note_source(a)
    flows.generate(flows.GenerateSettings(
        a.categories, a.client, a.prefix, a.out, a.source, a.bank, a.client_name, _only(a), a.decisions, a.marks,
        a.rulings, a.research, a.audience, a.yes_no, a.glossary, a.country, a.rebuild_existing, a.formulas,
        a.split_files, not a.no_ai_review, _model(a), proposals, answers, flows.load_brief(a.brief),
        benchmark=a.benchmark, benchmark_map=a.benchmark_map))


def cmd_check(a):
    """Spec 03: check a workbook after hand edits; never writes to it. Exit 1 on errors."""
    if flows.check(a.workbook, a.client, a.categories, a.against, a.yes_no, a.out, _model(a),
                   flows.load_brief(a.brief)).errors:
        sys.exit(1)


def cmd_privacy_check(a):
    _need(a.categories, "categories file", CATEGORIES_HOW)
    _note_source(a)
    flows.privacy_check(a.categories, a.client, a.prefix, a.source, a.bank, a.client_name, _only(a), a.notice)


def cmd_evaluate_model(a):
    _note_source(a)
    if a.categories:
        _need(a.categories, "categories file", CATEGORIES_HOW)
    flows.evaluate_model(a.source, a.bank, a.client, a.categories, a.model, a.limit, a.workers, a.cache,
                         a.dry_run, not a.no_review)


def cmd_evaluate(a):
    _need(a.categories, "categories file", CATEGORIES_HOW)
    _note_source(a)
    flows.evaluate(a.source, a.bank, a.client, a.categories)


class _Print(logging.Handler):
    """The flows' report lines, printed as they come (to the stdout of the moment, so tests can capture it)."""

    def emit(self, record):
        print(record.getMessage())


def main(argv=None):
    P = flows.current()
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
    p.add_argument("--sheet", action="append",
                   help="a tab holding the draft; give it more than once to read several tabs as one draft "
                        "(default: the first visible tab with a list)")
    p.add_argument("--previous", help="the client's previous category list (.xlsx/.csv): its names only, as "
                                      "evidence; categories missing from the draft are proposed")
    p.add_argument("--previous-sheet", action="append",
                   help="a tab of --previous to read (default: its results tabs, or its first list)")
    p.add_argument("--previous-label", default="", help="what to call the previous list, e.g. 2025-2026")
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
    p.add_argument("--brief", help="what the client is looking for, as text or a .txt file: read first by the model "
                                   "(above past sheets; the template rules still apply)")
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
    p.add_argument("--brief", help="what the client is looking for, as text or a .txt file: read first by the model "
                                   "(above past sheets; the template rules still apply)")
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
    p.add_argument("--values", dest="formulas", action="store_false",
                   help="write sub-totals as values instead of =SUM() formulas (the default, spec 17 C)")
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
    p.add_argument("--benchmark", help="the client's sample criteria (.xlsx, one tab per kind of category): each "
                                       "category starts from its group's tab, questions and marks")
    p.add_argument("--benchmark-map", help="group,kind,tab CSV (default <out>_benchmark_map.csv): proposed where "
                                           "a group has no line, then yours to correct")
    p.add_argument("--brief", help="what the client is looking for, as text or a .txt file: read first by the model "
                                   "(above past sheets; the template rules still apply)")
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
    except (flows.LLMError, flows.PrivacyError) as e:
        sys.exit(f"model step stopped: {e}")
    except flows.FlowError as e:
        sys.exit(str(e))
    finally:
        flows.log.removeHandler(handler)


if __name__ == "__main__":
    main()
