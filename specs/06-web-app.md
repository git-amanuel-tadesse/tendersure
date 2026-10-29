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
- These rules are checked, not just written down. On the Python side,
  `import-linter` contracts in the root `pyproject.toml` run as part of
  `make test` (`make imports` alone):
  - core imports no app, no argparse and no web framework;
  - apps import only `tendersure.flows` from core, checking direct imports only,
    since flows itself imports the rules;
  - only apps import `tendersure.flows`, so no rule module reaches up;
  - the API and the CLI are independent of each other, a contract added with
    the API.
  On the web side, an ESLint `no-restricted-imports` / `no-restricted-globals`
  rule does the same job.

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

- Every location core uses (data folder, bank, learned files, cache,
  outbox, reports, privacy terms, `.env`) comes from one `Paths` value,
  `tendersure.paths.current()`, read when it is needed and never at import.
  There are no `"data/..."` or `".cache/..."` literals elsewhere.
- The default home is `$TENDERSURE_HOME`, else the repo root (marked by
  `[tool.tendersure]` in the root `pyproject.toml`), so the CLI and the API
  read and write the same bank and learned files from any working directory.
  An app that wants another home calls `paths.use(Paths(...))` once at start.
  The tests point it at a temporary folder.
- `.env` stays at the root and is read by the apps, not by core's modules
  at import time.

### M4. Tooling

- Python: one virtualenv at the root, the three packages installed editable
  (`pip install -e packages/core -e apps/api -e apps/cli`); `requirements.txt`
  is replaced by the packages' `pyproject.toml` files.
- JavaScript: pnpm workspace over `apps/web` and `packages/api-client`.
- `make dev` runs Flask (:8765) and Vite (:5173, proxying `/api`) together.
- Files are moved with `git mv` so their history follows them.

## Architecture

```
apps/web  (TanStack Router + Query + Table)
  │  packages/api-client (generated types, typed fetch)
  │  dev: Vite proxy → :8765        local use: served by Flask from apps/web/dist
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
- reports each line a person should see through the `tendersure` logger
  (`log.info`), not `print`. The six model-fallback messages in `refine.py`,
  `naming.py`, `yesno.py`, `research.py` and `check_ai.py` do the same, so no
  callback has to be threaded through the rule modules. The CLI attaches a
  handler that prints the lines unchanged. The API attaches one per job and
  keeps them as the job's progress;
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

- Started with `tendersure-api [--port 8765]` (or `make serve`); opens the
  browser. Serves the built frontend from `apps/web/dist` and the API under
  `/api`.
- Binds to `127.0.0.1`, with no option to listen anywhere else.
- Rejects a request whose `Host` is not `localhost`, `127.0.0.1` or `[::1]`
  (DNS rebinding).
- Rejects a request whose `Origin`, when the browser sends one, is another
  site.
- Requires an `X-TenderSure: 1` header on every request other than
  GET/HEAD/OPTIONS. A page on another site cannot set that header without a
  CORS preflight, and the server never grants one.
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
- An uploaded file keeps the name it was sent with, or is refused. A name
  with a folder in it, `..`, a leading dot or an unusual character is
  refused rather than cut down, and only `.xlsx`, `.csv` and `.pdf` are
  accepted, at most 100 MB. Uploads go to `uploads/` and never overwrite:
  a second `draft.xlsx` becomes `draft (2).xlsx`.
- A download resolves inside its exercise's folder or is a 404, never a file
  elsewhere (`..`, links, `exercise.json` itself).
- Exercise ids are readable slugs of the name (`ERCS 2026–2029` →
  `ercs-2026-2029`); a repeated name gets `-2`.

### W4. Jobs

- One background worker thread per server (steps share the bank and the
  learned files, so they run one at a time; a second request queues).
- A job's lines are every line the `tendersure` logger emits while it runs,
  the same lines the CLI prints. They come from the worker or from threads
  the step starts itself (e.g. research resolving links in parallel), never
  from a thread answering an HTTP request.
- Each job is written to `workspaces/.jobs/<job>.json` on every change, so a
  page reload shows it and a crash loses no line. Jobs share one folder
  because some, like ingest, belong to no exercise; each job records its
  `exercise_id`.
- A job that was queued or running when the server stopped is shown as
  `interrupted`. Rerunning generation reuses the model answers already
  cached (`.cache/build_*`).
- The UI polls `GET /api/jobs/<id>` (TanStack Query `refetchInterval`) while
  a job runs. No websockets.

## The screens

### S1. Home

Exercises list (create: client code, client name, prefix), bank status
(workbooks, sheets, questions, last ingest), model status (configured /
off and why, using the same `_model_wanted` rule as the CLI).

### S2. Bank

Add workbooks: pick a folder or several `.xlsx` files. They are copied to a
staging folder (`workspaces/.staging/`), ingested as a job, and removed;
the bank keeps each workbook's bytes. The page shows the ingest summary and
links to download `validation_report.xlsx` and `bank.xlsx`.

The bank knows a workbook by its path: new content at the same path
replaces the old version. Uploaded workbooks are therefore recorded as
`browser/<path inside the picked folder>`, not by the staging path, which
changes every time. Uploading the same folder again replaces what changed,
as a re-ingest of the folder from the CLI does. A workbook ingested once
from the CLI and once from the browser is known under two paths; identical
content is still skipped by its hash.

### S3. Refinement (flow 1)

1. Upload the draft list (`.xlsx`/`.csv`; pick the tab if several). The
   parsed draft is shown (number, description, group) before anything runs.
2. Run: optional target (e.g. 50–60), model on/off. Job progress shown.
3. **Review table**, one row per revised line: status badge (Kept, Renamed,
   Merged, Separated, Repeated, Additional), description, from-draft numbers,
   reason, question for the client, sub-categories, eligibility, applies to,
   the model's suggested eligibility (shown, not applied). Editable:
   Decision (accept / reject / remove / edit), Client comment (with edit:
   the new wording, as in the workbook) and the team's Category Code.
   Filter by status, by group, and by "has a question for the client".
   The target count against the current count is shown at the top.
4. Download the revised list as Excel (to send to the client); upload an
   answered one back: its decisions and comments replace the browser's
   after a confirmation listing what changes.
5. **Apply**: runs `apply_review`; shows the final list, problems, and the
   client comments with the model's proposed change. Saves the final list
   and writes the exercise's categories table.

### S4. Categories table

The table generation reads: number, description, group, sub-categories, and
the three team columns (eligibility, gate question, applies to). Editable in
place. Every answer carries the table's problems, row by row:
- the T19 rule (Gate Question TRUE exactly when Individual Consultant is in
  Applies To): an error;
- a number used twice: an error;
- an empty description: an error;
- an organization type that is not one of the four: a warning.

The T19 check is the same function generation applies (`flows.gate_problem`),
so a mismatch is fixed here instead of turning up as NOT WRITTEN. A table
with problems is still saved, since the team fixes it over several edits.
Applies To is stored in canonical names (`plc` → Private Limited Company).
Import from the refinement's final list, or from a CSV; the table it
replaces is kept in `history/`. Export to CSV.

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
   (the `--review` path: no new model call for proposals). Answers the run
   already carried are kept, as they are in its QA file. Each run is kept in
   `runs/<n>/` (settings, result, proposals, answers, workbooks), so the team
   can compare and download any earlier one.
5. An answer that cannot be applied says why in its Status, e.g. "NOT
   APPLIED on 4: section 'technical' is not in this sheet". Before this
   change the QA workbook showed the sheet's total instead of the reason.

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

## As built (2026-10-29)

Where the build differs from the plan above, and why:

- **Web boundary check.** It's a script (`scripts/check-web-imports.mjs`, in
  `make test`), not an ESLint rule. There is no `fetch`, `XMLHttpRequest` or
  `"/api/..."` path in `apps/web/src` except as a typed `api.GET(...)` call;
  download links come from api-client's `links`, which are type-checked
  against the contract. ESLint was left out because its TypeScript plugin
  lags the TypeScript and Vite versions in use.
- **TypeScript 5.9, not 7.** openapi-typescript generates the types through
  TypeScript's JavaScript API, which TypeScript 7 (native) does not have.
- **TanStack Table 8.21.** Version 9 is a new API; the one table component
  (`DataTable`) is where a move to v9 would happen.
- **Two type sets in api-client.** `Inputs` (what the app sends) leaves a
  field with a default optional, so a PATCH sends only what changed.
  `Schemas` (what the app reads) has every field present, as the API always
  sends them.
- **Schema names are unique.** OpenAPI names a schema after its pydantic
  class, and two `Import` classes had silently shared one schema. A test now
  fails on any repeated model name in the API.
- **Port 8765, not 5000.** On macOS the AirPlay Receiver holds 5000 on every
  address. `http://localhost:5000` tries IPv6 first and got AirPlay's empty
  403, even though the server answered on 127.0.0.1. The server now opens
  127.0.0.1, and refuses to start on a port another program holds.
