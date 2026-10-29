# TenderSure criteria toolkit

Parse, validate and bank TenderSure supplier-prequalification criteria sheets,
and generate new sheets for a client's categories in their existing Excel
format.

Read `SPEC.md` first — it carries the domain knowledge derived from 66 real
sheets (column semantics, answer-type codes, corpus statistics, scope
boundary, open questions). Most of it is not recoverable from the code alone.
`specs/` holds the agreed specs from the team's October 2026 feedback (the
21-column template, Eligibility, gate questions, short names, Yes/No rules,
category refinement, the check command, learning from the bank).

## Install

Needs Python 3.11+ and Node 20+ with pnpm.

    make install                    # .venv at the root (core, CLI, API editable) and pnpm install
    source .venv/bin/activate       # puts `tendersure` and `tendersure-api` on the PATH

The repo is a monorepo (spec 06), one folder per party:

| Folder | Party | May use |
|---|---|---|
| `packages/core` | `tendersure`: every rule, the bank, and `tendersure.flows`, the steps every app calls | openpyxl, pdfplumber |
| `apps/cli` | `tendersure` command: arguments in, flows out | `tendersure.flows` only |
| `apps/api` | `tendersure-api`: the local server (Flask, 127.0.0.1 only): HTTP in, flows out | `tendersure.flows` only |
| `packages/api-client` | the TypeScript client: types generated from the API's OpenAPI document | - |
| `apps/web` | the web app (React, TanStack Router, Query, Table) | `@tendersure/api-client` only |

`make test` checks those boundaries (import-linter and a web check), and that
the committed API contract matches the API. `.env` and `data/` live at the
repo root (or wherever `$TENDERSURE_HOME` points), so commands work from any folder.

## The web app

    make serve      # builds the web app; the API serves it at http://localhost:5000/
    make dev        # while working on it: the API on :5000 and Vite on http://127.0.0.1:5173/

An exercise is one client's round. Its three steps follow the CLI's:
**1 Refine categories** (draft list → revised list → the client's review → final list),
**2 Categories table** (edited in place, each row checked: a gate mismatch shows
on its row), **3 Generate criteria** (runs, the Decisions answered in the browser,
applied as the next run). The Excel workbooks still go to and come back from the
client. Everything stays on this machine, in `workspaces/` (git-ignored); the
bank and the learned files are shared with the CLI.

After changing the API, regenerate its contract and the web app's types:

    make contract

## Run

Client workbooks are confidential: keep them out of the repo (`sample file/`
is git-ignored) and point the commands at their folder. For years of history,
any folder works, with subfolders (e.g. one per year).

    # 1. Put the history in the bank (data/bank.sqlite) and write the reports.
    #    Run it again whenever workbooks are added: only new or changed files are read.
    tendersure ingest "sample file" --out reports
    #    -> reports/validation_report.xlsx   findings by rule, by sheet
    #    -> reports/bank.xlsx                answer-type reference, bank, conflicts

    # 2. Category table from the client's notice
    tendersure categories "sample file/ERCS Supplier Prequalification 2026-2029 Invitation.pdf" --client ERCS --version 2026-09-ext \
        --out data/ercs_categories.csv [--diff data/ercs_categories_old.csv] \
        [--engine layout|llm|both]
    #    layout: table geometry, no network (fits the ERCS notice)
    #    llm:    a model reads the PDF; every word is checked against the PDF text
    #    both:   run both, reconcile per category, report disagreements
    #    The team then fills three columns per category (blank = not set):
    #      eligibility    documents for the Eligibility section, separated by " | "
    #      gate_question  TRUE when Organization type gates the sheet
    #      applies_to     organization types accepted, comma-separated

    # 2b. Refine a draft category list before it goes to the client (spec 01)
    tendersure refine "Red Cross Categories 1st Draft.xlsx" --client ERCS --target 50-60
    #    -> reports/<draft> - Revised.xlsx   every draft row with a status (Kept, Renamed, Merged,
    #       Separated, Repeated, Additional (suggested)), reasons, questions for the client and the
    #       proposed Eligibility / Gate Question / Applies To. Answer its Decision column
    #       (accept, reject, remove, edit + Client comment), then:
    tendersure refine --client ERCS --apply "reports/<draft> - Revised.xlsx" \
        --categories-out data/ercs_categories.csv
    #    -> the final list, and the categories table step 3 reads

    # 2c. After ingesting history: learn names and Yes/No verdicts for every question (spec 04)
    tendersure learn            # only questions not seen before go to the model
    #    -> reports/learned_review.xlsx   answer it, then: learn --review reports/learned_review.xlsx
    #    The learned files (data/glossary.csv, data/yesno_polarity.csv) are client data, kept out of git.

    # 3. Generate all categories in one pass
    tendersure generate --categories data/ercs_categories.csv \
        --client ERCS --prefix ERCS-ET-2026- --out out/ERCS-2026-2029.xlsx
    #    -> out/ERCS-2026-2029.xlsx      one sheet per category, upload format
    #    -> out/ERCS-2026-2029_qa.xlsx   the review: one Decisions tab (~30 lines
    #                                    for 58 sheets) plus optional Detail tabs

    # 3b. Review: answer the Decisions tab of the QA report, then apply it
    tendersure generate --categories data/ercs_categories.csv \
        --client ERCS --prefix ERCS-ET-2026- --out out/ERCS-2026-2029.xlsx \
        --review out/ERCS-2026-2029_qa.xlsx

    #    Every sheet is written in the 21-column template and passes the check
    #    rules below before it is written (a sheet with an error is NOT WRITTEN).
    #    -> out/ERCS-2026-2029_learned_review.xlsx   names and Yes/No verdicts drafted
    #       for this batch, to confirm (learn --review)

    # 3c. After the team edits a workbook by hand: check it before upload
    tendersure check "out/ERCS-2026-2029.xlsx" --client ERCS \
        --categories data/ercs_categories.csv [--against <generated workbook>] [--no-model]
    #    -> reports/<name>_check.xlsx   every problem by sheet and row; exit code 1 on errors.
    #    Never changes the workbook. --against lists what changed since generation.

    # 4. How good is it? Leave-one-out against existing sheets
    tendersure evaluate --client ERCS --categories data/ercs_categories.csv

    make test                       # synthetic data; corpus tests run if "sample file/" exists


### Generate options

| Flag | Effect |
|---|---|
| `--only 3,13,31` | just these category numbers |
| `--decisions reports/bank.xlsx` | apply rulings typed into the Decision column of the Conflicts sheet |
| `--review qa.xlsx` | apply the answers in the QA report's Decisions tab |
| `--rulings path` | standing rulings file (default `data/rulings.csv`) |
| `--marks marks.csv` | override the section split per category: CSV `code,section,marks` (the Decisions tab is usually easier) |
| `--rebuild-existing` | ignore a past sheet with the same code instead of reusing it |
| `--formulas` | `=SUM()` subtotals instead of numbers (see SPEC §7) |
| `--split-files` | one workbook per category |
| `--model id` | use a different model than `TENDERSURE_MODEL` (the AI runs automatically whenever a model is configured) |
| `--no-model` | build without any AI step |
| `--no-ai-review` | stop after the proposals (rewordings applied) and leave the rest to a person |
| `--glossary`, `--yes-no`, `--audience` | the learned files (defaults under `data/`; seeded from the bank when absent) |

## Model steps: privacy first, any provider

**Nothing identifying leaves the machine.** Every model request is anonymised
inside the model client (`privacy.py`), so no step can skip it:

- client names and aliases become `[CLIENT]` (the client being worked on) and
  `[CLIENT-B]`, `[CLIENT-C]`… for the others; every client found in the corpus
  is registered automatically
- sheet codes become `[REF-n]`; emails, URLs and phone numbers are replaced
- notices: only the category table is sent, never the narrative (which
  identifies an organisation even without its name) and never the raw PDF
- extra names to hide go one per line in `data/privacy_terms.txt`
- if anything identifying survives, the request is **refused**, not sent
- the exact text sent is logged in `.cache/llm_outbox/` for audit

See what would be sent, without sending anything:

    tendersure privacy-check --categories data/ercs_categories.csv \
        --client ERCS --prefix ERCS-ET-2026- --notice "sample file/<notice>.pdf"

Anonymisation hides *who*; the question wording itself is still the clients'
work. So providers are restricted to ones that do not train on it:

| Backend | Setting | Guarantee |
|---|---|---|
| Gemini, your own key | `TENDERSURE_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_PAID_TIER=yes` | refuses to run unless you confirm a paid-tier key (unpaid: Google may use prompts and humans may read them) |
| OpenRouter | `TENDERSURE_PROVIDER=openrouter`, `OPENROUTER_API_KEY` | every request carries `data_collection: deny` and `zdr: true` |

Copy `.env.example` to `.env` and fill it in (git-ignored).

**Fully automatic.** Whenever a model is configured in `.env`, every build
runs the AI (no flag needed; `--no-model` turns it off). Two passes run per
category: a proposer suggests rewordings, removals and additions; an
independent reviewer checks each one against evidence from the bank ("asked
in 0 of 4 past questionnaires on similar topics") and agrees, disagrees,
edits, or says a rewording should be a removal. Code applies only what is
agreed - a removal always needs two signals (proposer + reviewer, or
reviewer + bank). Disagreements keep the client's past wording and are
recorded. The QA report becomes an audit trail of every AI decision with its
reason; a person can still override any line and rerun with `--review`, but
doesn't have to. `--no-ai-review` stops after the proposals.

**Where the model fits.** It proposes; the reviewer decides; code applies.
With `--model`, each new category gets one call. The model's answers appear
as the *Proposed* answer on `Q-` lines (e.g. "remove: 11, 12, 13") and as
`A-` lines of questions to add, including drafted ones for categories the
corpus says nothing about. A blank answer accepts a proposal. Proposals are
stored in the QA report, so `--review` replays exactly what the reviewer saw
with no further model calls.

Whatever the model, the code keeps the last word: only flagged rows can be
dropped, suggestions keep their corpus fields, drafted questions arrive with
0 marks for review, and extracted category text must appear verbatim in the
PDF.

## The review

The QA report's **Decisions** tab is the whole review. Repetition is
collapsed: a question carried into 19 categories is one line, categories
copied from the same sheet share one marks line, and a rule that recurs
across sheets is one team-wide line. For the ERCS batch: ~1,600 rows of
detail become 29 decisions.

| ID | Answer with | Effect |
|---|---|---|
| `Q-…` | blank / `remove all` / `remove: 3, 7` / `keep: 1, 2` / `reword: 9` / `reword 9: own wording` (combine with `;`) | removes the question(s) from those categories (the section keeps its marks), or rewords it for a category while keeping its answer type, flags and marks. Trade reference, declaration and submission questions are never proposed for removal |
| `M-…` | blank / `technical 10, trade_reference 35` | sets those section marks on every listed category; must still total 100 |
| `M-FREED-…` | same as `M-` | appears when a removal empties a section: its marks were spread proportionally and need confirming |
| `A-…` | blank / `add all` / `add: 1, 3` / `skip` | adds questions to one category: from a past sheet on the same topic, or drafted by the model (marked NEW, 0 marks) |
| `N-…` | (information) | categories with no past sheet on their topic; run `--model` to get questions drafted |
| `C-…` | a value (`true`, `4`, `compliance`…) or `majority` | settles a client's inconsistent field; saved to `data/rulings.csv` for good |
| `T-…` | `keep` / `set flags` | decided once for the batch |

Rerun `generate … --review <qa file>`. Every answer is applied by code to
every affected sheet; the Status column says `applied`, `accepted` or why it
was not (a rejected marks answer leaves that sheet's previous marks in place).
Answers carry forward under stable IDs, so the file is the record of what was
decided and can be answered in several sittings. Nobody edits the 58 sheets
by hand.

## Years of history: no QA on ingest

Past sheets are evidence, not deliverables; an error in one only matters if
it is copied. So ingest needs no human review, however much history goes in:

- **Every sheet is graded automatically** (`trust.py`): clean, fixed on reuse,
  quarantined (broken marks or identity: never copied, wording still counts),
  or superseded (an earlier copy). The validation report opens with a Health
  tab by client and year.
- **Within a client, the majority is the convention.** If 90% of a client's
  uses agree (80% over 10+ uses), a copied row that disagrees is corrected on
  reuse and the change noted. More history means more of these settle
  themselves.
- **A real disagreement is asked only when a batch uses it**, as a `C-` line in
  that batch's Decisions tab (or all at once: `T-ADOPT-MAJORITY` = `yes`).
- **Every answer becomes a standing ruling** in `data/rulings.csv`, applied to
  every later batch, so nothing is asked twice. Keep this file in your private
  repo: it is the team's accumulated judgement.
- Corpus folders may have subfolders (one per year); parsed workbooks are
  cached in `.cache/` and re-read only when a file changes.

## The bank

`data/bank.sqlite` is the historical record: every past criteria sheet, its
questions, grades, and the workbook it came from (for the client's styling).
`ingest` adds to it; everything else reads from it, never from the original
folder, so the history can be archived once ingested. It holds client data
and is git-ignored. A folder can still be passed instead of the bank for a
one-off look (`generate "some folder" ...`).

The build, per category: pick the closest past sheets in the bank by the
category's description, notice group and any `requirements` (an optional
column in the categories file, e.g. "must ask for ISO 22000"); copy the
client's own structure; with `--model`, **reword copied questions for the new
category during the build** (answer type, flags and marks are kept); propose
removals and additions in the QA file; write the prequalification workbook.

## How a sheet is generated

1. Rank past categories against the new description: text similarity, plus
   same client, same advert group, same procurement kind (`similarity.py`).
2. The best match from the same client is the skeleton; its rows are copied
   with every field they carry.
3. Client names are swapped, conflict rulings applied, and the two
   unambiguous answer-type errors corrected. Every change is noted on the row.
4. Rows rare in that client's sheets are flagged as specific to the
   skeleton's category — the rows the reviewer reads.
5. Rows from the next closest categories are offered as suggestions.
6. Marks come from the skeleton unless overridden; a sheet that does not
   total 100 is not written.
7. The template rules (specs/02) are applied once nothing else will change
   the rows: Gate Question and Applies To from the categories table; the
   sub-category question from the advert; an Eligibility section with the
   Trade License and the category's own documents; marks repeated in System
   or Compliance; option scores scaled to the marks; Yes/No answer types
   from whether Yes is the good answer; a Short Name and Acronym on every
   upload or flagged row.
8. Every sheet passes the check's rules (the same code `check` runs) before
   it is written; a sheet with an error is listed as NOT WRITTEN.

## Layout

    pyproject.toml   monorepo root: shared tool settings, marks where .env and data/ live
    Makefile         make install | test
    packages/core/   the tendersure package: pyproject.toml, src/tendersure/, tests/
    apps/cli/        the tendersure command (tendersure_cli): pyproject.toml, src/, tests/
    apps/api/        the local API server (tendersure_api, Flask): pyproject.toml, src/, tests/
    data/            the bank and learned files (client data, git-ignored)

    packages/core/src/tendersure/flows/   the steps: one function per step, plain inputs, a result
                     back, report lines through the "tendersure" logger; never prints or exits
      ingest.py categories.py refine.py research.py learn.py generate.py check.py evaluate.py
      common.py      where the sheets come from, which model runs (Model), the learned files

    packages/core/src/tendersure/
      paths.py       where .env and data/ live ($TENDERSURE_HOME, else the repo root)
      schema.py      column map, answer-type codes, section keys, constants
      parser.py      workbook -> structured Row objects (deterministic)
      validate.py    corpus rules (platform vs silent) and the template rules (check + fix)
      bank.py        question bank + corpus-consensus conflict detection
      categories.py  notice PDF -> category table by layout (number, group, sub-categories), diff
      category_llm.py  model extraction of the same table, verbatim check, reconcile
      llm.py         provider-agnostic JSON-out model access via OpenRouter
      similarity.py  requirement-level retrieval (TF-IDF; embedder is pluggable)
      generate.py    plan a sheet: skeleton, corrections, flags, suggestions, marks
      writer.py      plan -> Excel in the skeleton's own styling
      reports.py     validation report, bank workbook, QA report, decision loader
      review.py      the Decisions tab: collapse the review, apply the answers
      trust.py       sheet grades, client conventions, standing rulings
      selectors.py   optional model step (selects and drafts; code applies)
      gating.py      Gate Question and Applies To; who answers each question (audience)
      naming.py      Archive Short Names and Acronyms (letter rule, model drafts)
      glossary.py    the glossary of short names and acronyms
      yesno.py       whether Yes is the good answer; the Yes/No answer-type rule
      grouping.py    rewordings of one question
      learned_store.py  storage driver for the learned files (CSV today)
      learned_review.py the review workbook for learned drafts
      refine.py      category refinement: draft list -> revised -> final
      research.py    research: technical names, questions, licences, documents (bank first, then the web)
      check.py       check a workbook after hand edits
      check_ai.py    the AI read of a checked workbook (warnings only)
      evaluate.py    leave-one-out evaluation
      testing.py     synthetic workbooks for every party's tests (no client data)
    packages/core/tests/   pytest, synthetic workbooks

## Rules to keep

- The model selects; the code assembles. Never let a model write Excel rows.
- No provider lock-in: model access only through `llm.py`.
- Validate on ingest. Nothing unvalidated enters the bank.
- The bank suggests, never enforces.
- Marks are a client decision. Code enforces the 100 total; a human sets
  the split.
