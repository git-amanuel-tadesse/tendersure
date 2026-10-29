# Spec 06: Web app (Flask + TanStack)

A browser UI for the two flows the team runs most: **category refinement**
(spec 01) and **criteria generation** (spec 02), plus the setup generation
needs (the bank and the categories table). The rules in specs 01–04 do not
change; this spec only changes how the team reaches them.

## Agreed choices (2026-10-29)

- **Local first.** Each team member runs the app on their own machine; the
  server listens on `127.0.0.1` only. No logins, no hosted storage. Client
  workbooks never leave the machine except through the existing privacy
  layer. A shared server is a later spec (it needs auth and retention rules).
- **Review in the browser.** The Decision column of the revised list and the
  Decisions tab of the QA report become editable tables. The Excel files stay:
  they can be downloaded at any point (to send to the client) and an answered
  one can be uploaded back, so a client who replies in Excel still works.
- **Scope of the first version.** Refinement, generation, a bank page
  (ingest) and a categories-table page. `check`, `learn`, `evaluate`,
  `privacy-check` and `categories` from a PDF stay CLI-only for now.
- **The CLI stays.** It and the web app call the same service functions, so
  a rule cannot behave differently between the two.
- **Monorepo with clean separation.** Core, API, CLI and web each live in
  their own package with their own dependencies and tests; who may import
  whom is enforced by tooling (M1).

## Words

- **Exercise**: one client's prequalification round, e.g. "ERCS 2026–2029".
  It holds the client code, client name, sheet code prefix, and everything
  produced along the way: draft list → revised list → final list →
  categories table → generation runs.
- **Bank** and **learned files** (`data/bank.sqlite`, `data/glossary.csv`,
  `data/yesno_polarity.csv`, `data/audience.csv`, `data/rulings.csv`): shared
  by every exercise, as today.
- **Job**: one long step run in the background (ingest, refine, generate).
  It has a status, progress lines and a result.

## Monorepo

One repository, four parties, each in its own folder with its own
dependencies, tests and entry point. `apps/` are thin adapters; the logic
lives in `packages/`.

```
tender-sure/
├── packages/
│   ├── core/            Python package `tendersure`: the rules (parser, bank, generate,
│   │                    refine, check, llm, privacy...) and `tendersure.flows`, the steps.
│   │                    pyproject.toml, src/tendersure/, tests/
│   └── api-client/      TypeScript: types generated from the API's openapi.json + a
│                        typed fetch client. package.json, src/
├── apps/
│   ├── api/             Python package `tendersure_api`: Flask, pydantic schemas, jobs,
│   │                    workspaces. pyproject.toml, src/tendersure_api/, tests/
│   ├── cli/             Python package `tendersure_cli`: argparse → flows → print.
│   │                    pyproject.toml, src/tendersure_cli/, tests/
│   └── web/             TanStack SPA (Router + Query + Table, Vite, React, TS).
│                        package.json, src/
├── data/                runtime data shared by every party (bank, learned files); git-ignored
├── workspaces/          exercises (W3); git-ignored
├── specs/  SPEC.md  README.md  .env.example
├── pyproject.toml       dev tooling only (pytest, import-linter); no code
├── package.json  pnpm-workspace.yaml   (apps/web, packages/api-client)
└── Makefile             make install | dev | test | build | contract
```

### M1. Who may depend on whom

```
apps/web ──▶ packages/api-client ──HTTP──▶ apps/api ──▶ tendersure.flows ──▶ tendersure.*
                                           apps/cli ──▶ tendersure.flows ──▶ tendersure.*
```

- **core** knows nothing of the parties above it: no Flask, no argparse, no
  `print`, no `sys.exit`, no paths relative to the working directory. Its
  dependencies stay openpyxl and pdfplumber.
- **api** and **cli** import only `tendersure.flows` (and its result and
  error types), never the rule modules directly. Neither imports the other.
- **web** reaches the backend only through `api-client`; no hand-written
  `fetch` to `/api` elsewhere, no Python types copied by hand.
- These rules are checked, not just written down: `import-linter` contracts
  for the Python side (part of `make test`), an ESLint
  `no-restricted-imports` / `no-restricted-globals` rule for the web side.

### M2. The contract between api and web

- The API's requests and responses are pydantic models in `apps/api`
  (`schemas/`), mapped from core's dataclasses there. Core keeps plain
  dataclasses and never learns about JSON shapes.
- The API publishes its OpenAPI document (flask-openapi3). `make contract`
  writes it to `packages/api-client/openapi.json` and regenerates the
  TypeScript types (openapi-typescript); both are committed.
- `make test` fails if the committed contract is out of date, so a backend
  change that breaks the frontend shows up as a diff, not a runtime error.

### M3. Configuration and runtime data

- Core takes a `Paths` value (data folder, cache folder, outbox) instead of
  the literal `"data/..."` and `".cache/..."` strings it uses today
  (`store.py`, `learned_store.py`, `privacy.py`, `llm.py`, `check_ai.py`).
- Each app builds `Paths` from `TENDERSURE_HOME` (default: the repo root), so
  the CLI and the API read and write the same bank and learned files.
- `.env` stays at the root and is read by the apps, not by core's modules
  at import time.

### M4. Tooling

- Python: one virtualenv at the root, the three packages installed editable
  (`pip install -e packages/core -e apps/api -e apps/cli`); `requirements.txt`
  is replaced by the packages' `pyproject.toml` files.
- JavaScript: pnpm workspace over `apps/web` and `packages/api-client`.
- `make dev` runs Flask (:5000) and Vite (:5173, proxying `/api`) together.
- Files are moved with `git mv` so their history follows them.

## Architecture

```
apps/web  (TanStack Router + Query + Table)
  │  packages/api-client (generated types, typed fetch)
  │  dev: Vite proxy → :5000        local use: served by Flask from apps/web/dist
  ▼
apps/api  (Flask blueprints: bank, exercises, refine, categories, generate, jobs;
  │        pydantic schemas; job runner; workspaces)
  ▼
tendersure.flows  (one function per step, typed inputs and results,
  │                a progress callback instead of print)
  ▼
tendersure.*  (existing modules, unchanged rules)
         ▲
apps/cli  (argparse → flows → print)
```

### W1. Service layer first

The orchestration now inside `cli.py` (`cmd_refine`, `cmd_generate`,
`cmd_ingest`, …) moves to `tendersure.flows`, one module per flow. Each
function:

- takes plain inputs (paths, dataclasses, dicts, `Paths`), not `argparse`
  namespaces;
- reports progress and warnings through a `progress(message)` callback, not
  `print` (including the five model-fallback messages in `refine.py`,
  `naming.py`, `yesno.py` and `check_ai.py`);
- returns a result dataclass (counts, output paths, problems, NOT WRITTEN
  list) instead of printing a summary;
- raises an error type instead of `sys.exit`.

`apps/cli` keeps the commands and their output, calling these functions.
The command becomes `tendersure <command>` (a console script);
`python -m tendersure` and `run_ingest.py` go, since nothing outside the
repo depends on them yet. Generation gains in-memory inputs for the review:
`proposals` and `answers` as dicts, so the API can apply browser answers
without writing an Excel file first (`--review <file>` still works by
reading the file into the same dicts).

### W2. Flask server

- Started with `tendersure-api [--port 5000]` (or `make serve`); opens the
  browser. Serves the built frontend from `apps/web/dist` and the API under
  `/api`.
- Binds to `127.0.0.1`. Rejects requests whose `Host` is not
  `localhost`/`127.0.0.1` (DNS rebinding), and requires an
  `X-TenderSure: 1` header on every non-GET request (a page on another site
  cannot set it without a CORS preflight, which the server never grants).
- JSON everywhere; errors as `{"error": "...", "detail": ...}` with a 4xx/5xx.
- Never returns secrets: the settings endpoint says which provider and model
  are configured and whether the paid-tier guard passes, never the key.

### W3. Storage

- `workspaces/<exercise-id>/` (git-ignored: client data) holds
  `exercise.json` and the exercise's files: uploaded drafts, revised and
  final lists, `categories.csv`, and one folder per generation run
  (`runs/<n>/`: workbook, QA report, `proposals.json`, `answers.json`).
- Browser edits are saved as JSON next to the files they belong to
  (`revised.json`, `answers.json`); the Excel files are written from that
  state on download. The JSON is the source of truth inside the app.
- Upload names are sanitised; only `.xlsx`, `.csv` and `.pdf` are accepted.

### W4. Jobs

- One background worker thread per server (steps share the bank and the
  learned files, so they run one at a time; a second request queues).
- A job's progress lines and status are kept in memory and written to
  `workspaces/<id>/jobs/<job>.json`, so a page reload shows them. A job that
  was running when the server stopped is shown as `interrupted`; rerunning
  generation reuses the model answers already cached (`.cache/build_*`).
- The UI polls `GET /api/jobs/<id>` (TanStack Query `refetchInterval`) while
  a job runs. No websockets.

## The screens

### S1. Home

Exercises list (create: client code, client name, prefix), bank status
(workbooks, sheets, questions, last ingest), model status (configured /
off and why, using the same `_model_wanted` rule as the CLI).

### S2. Bank

Add workbooks: pick a folder or several `.xlsx` files; they are copied to a
temporary folder, ingested, and removed. Shows the ingest summary and links
to download `validation_report.xlsx` and `bank.xlsx`.

### S3. Refinement (flow 1)

1. Upload the draft list (`.xlsx`/`.csv`; pick the tab if several). The
   parsed draft is shown (number, description, group) before anything runs.
2. Run: optional target (e.g. 50–60), model on/off. Job progress shown.
3. **Review table**, one row per revised line: status badge (Kept, Renamed,
   Merged, Separated, Repeated, Additional), description, from-draft numbers,
   reason, question for the client, sub-categories, eligibility, applies to,
   the model's suggested eligibility (shown, not applied). Editable:
   Decision (accept / reject / remove / edit), Client comment, and the
   wording when the decision is edit. Filter by status, by group, and by
   "has a question for the client". The target count against the current
   count is shown at the top.
4. Download the revised list as Excel (to send to the client); upload an
   answered one back: its decisions and comments replace the browser's
   after a confirmation listing what changes.
5. **Apply**: runs `apply_review`; shows the final list, problems, and the
   client comments with the model's proposed change. Saves the final list
   and writes the exercise's categories table.

### S4. Categories table

The table generation reads: number, description, group, sub-categories, and
the three team columns (eligibility, gate question, applies to). Editable in
place. The T19 rule (Gate Question TRUE exactly when Individual Consultant
is in Applies To) is shown inline on each row, so a mismatch is fixed
before generation instead of reported as NOT WRITTEN. Import from the
refinement's final list, or from a CSV; export to CSV.

### S5. Generation (flow 2)

1. Settings: categories (all, or pick some: `--only`), model on/off, AI
   review on/off (`--no-ai-review`), rebuild existing, formulas, split files.
   Client, client name and prefix come from the exercise.
2. Run: job progress per category (the lines the CLI prints today), then the
   result: sheets written, NOT WRITTEN with reasons, downloads (workbook,
   QA report, learned review).
3. **Decisions table**: the QA report's Decisions tab, one row per item,
   grouped by kind (Q questions, A additions, M marks, C client conventions,
   N no source, T/F team-wide). Each kind gets controls that write the same
   answer text the Excel tab takes (spec 02 grammar, e.g. `remove: 3, 7`,
   `reword 9: …`, `add: 1, 3`, `technical 10, trade_reference 35`), with a
   free-text field for anything else. The Status column after applying is
   shown per row.
4. **Apply answers**: a new run with the saved proposals and the answers
   (the `--review` path: no new model call for proposals). Each run is kept,
   so the team can compare and download any earlier one.

## Out of scope for this version

Shared hosting and logins; editing criteria sheets cell by cell in the
browser (the team edits the workbook in Excel and runs `check`); the
`check`, `learn` and `evaluate` pages; PDF category extraction.

## Done when

- The CLI's commands give the same outputs as before the move (tests pass;
  an ERCS generate and a Red Cross refine produce identical workbooks).
- From the browser alone, a team member can: ingest the corpus, upload the
  Red Cross draft, refine it, answer the review, apply it, fix the
  categories table, generate the ERCS batch, answer its Decisions tab and
  apply them, and download every file.
- Nothing listens on anything but `127.0.0.1`; a POST without the header is
  refused; no API response contains a key.
- `make test` runs every party's tests, the import contracts and the
  contract-freshness check, and all pass.
