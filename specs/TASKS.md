# Tasks

The specs split into small tasks, in build order. Each task is one reviewable
change with its own test. "Depends" lists tasks that must land first; tasks
with no dependency between them can be built in any order.

Size: **S** under half a day, **M** about a day, **L** two days or more.

**Status (2026-10-29): T01-T41 are done; T42-T48 (research) are built; T49-T54 (web app, 6a: monorepo, Paths, flows, apps/cli, import contracts) and T55-T62 (apps/api, complete for both flows) and T63-T71 (api-client, the web app) are built.** The regenerated ERCS
batch meets the spec 02 acceptance list and `check` finds 0 errors on it;
see SPEC.md section 7 for the numbers. T09 needed no fix: the notice's five
sub-category lists were already read correctly.

## Design note: one rule, two uses

Every sheet rule in spec 02 is needed twice: generation applies it, and the
check (spec 03) verifies it on an edited file. Each rule is therefore written
once, as a pair in one place: a `find` that returns findings for a sheet, and
a `fix` that generation calls. `validate.py` already holds the corpus rules
and is where these go. The check command and the pre-write check (T25) call
the same `find` functions, so a rule cannot drift between the two flows.

## Phase 0: the new template

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T01 | Read fields by header label (14-, 16- and 21-column sheets); add `eligibility` to rows. *Code written during discussion; needs its tests.* | 02 G1 | | S | An NRC sheet parses with Eligibility TRUE on its 5 rows and System/Compliance in the right fields; re-ingesting the existing corpus gives the same 6,845 question rows and findings as before. |
| T02 | Write the 21-column layout: headers, `Marks` banner over E–I, field values by column, Kenya-only columns empty, Sub Total/Total over E, G, H. | 02 G1 | T01 | M | A generated sheet has the 21 headers in order and re-parses to the same rows as its plan. |
| T03 | Copy styles by field, not position; columns the skeleton lacks borrow a sibling's style. | 02 G1 | T02 | S | Output from a 14-column ERCS skeleton has the skeleton's fonts, fills and widths on the matching fields, and styled new columns. |

## Phase 1: generation rules

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T04 | Section order with an Eligibility section after Company Information, always present. | 02 G2 | T02 | S | Every generated sheet has sections in the G2 order. |
| T05 | Move the Trade License into Eligibility (insert the standard row if absent); set Eligibility, type 5, Mandatory, Compliance flag, 0 marks on every Eligibility row and Eligibility FALSE elsewhere. | 02 G3 | T04 | S | ERCS sheets: Trade License in Eligibility; registration certificate, TIN, tax clearance, VAT still in Registration. |
| T06 | Marks in two columns: every question row repeats Marks (0 included) in System or Compliance, by the G4 rule; blank Marks becomes 0. | 02 G4 | T02 | S | Regenerated ERCS batch has no row with Marks and blank System/Compliance (today: 3,101). |
| T07 | Inserted rows take the Sub Section of their neighbour in the same section. | 02 G6 | | S | The 27 split blocks in the ERCS batch are gone. |
| T08 | Borders on every cell B–V of question and total rows; outer border on each merged block. | 02 G6 | T02, T07 | S | Visual check of three sheets; a test asserts borders on merged-block edges. |
| T09 | Audit sub-category extraction against the ERCS notice; fix what the extractor misses. | 02 G5 | | M | Every sub-category printed in the notice is in the categories table; the categories that gain sub-categories are listed for the team. |
| T10 | Sub-category question: insert after the business-type question, replace a copied one's options, remove it when the category has none; commas inside a name become ` / `. | 02 G5 | T02 | S | Categories with sub-categories have exactly one such question with their own options; others have none. |
| T11 | Glossary: `data/glossary.csv` seeded from the bank (most common short name / acronym per question, client first). | 02 G8 | T01 | M | Seeded entries include TIN Certificate/TIN, Tax Compliance Certificate/TCC, Reference n/REF n; re-seeding keeps team edits. |
| T12 | Fill Short Name and Acronym on required rows from the glossary; draft missing pairs (saved as `drafted`, listed in QA); number repeated blocks; report pairs that contradict the glossary. | 02 G8 | T11 | M | No required row lacks either field; acronyms unique per sheet; ERCS "Website address" / "Main Office Location" reported. |
| T13 | Category eligibility documents become upload rows in Eligibility, named from the glossary. | 02 G3 | T05, T12 | S | A category with `eligibility = IATA Registration Certificate` gets that row, fully flagged and named. |
| T14 | Option-score rule: fix every violation (Yes/No by T16, others by proportional scaling), note it, list it in QA. | 02 G9 | T06 | S | No generated row breaks G9; each fix appears in the QA report. |
| T15 | Yes/No polarity: `data/yesno_polarity.csv`, seeded by the model (or left unknown without one); unknown rows listed in QA for the team. | 02 G10 | | M | Every Yes/No question in the ERCS batch has a polarity or a QA line asking for it. |
| T16 | Apply the Yes/No answer-type rule, overriding client convention and `trust.py` corrections. | 02 G10 | T15 | S | "Does your company recycle…" with marks → type 4; PEP and litigation with marks → type 2, `Yes,No`, `0,<marks>`. |
| T17 | Audience: `data/audience.csv` (question → business types) seeded from the SNV sheets. | 02 G7 | T01 | S | Company name, VAT, shareholder rows, full name per ID and ID/passport carry the SNV audiences. |
| T18 | Gate and Applies To from the categories table: business-type options, single Gate row, Applies To per row, individual-only rows added or removed, empty when no gate. | 02 G7 | T17, T02 | M | A gated test category matches the PDF screenshot pattern; a non-gated one has both columns empty. |
| T19 | Categories table check: `gate_question` must agree with Individual Consultant in `applies_to`; such a category's sheet is not written. | 02 G7 | | S | A mismatched row is reported by number and skipped. |

## Phase 2: the check command

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T20 | `check` command: read an edited workbook, findings with severity, summary, report workbook, non-zero exit on errors; never writes to the input. | 03 | T01 | M | Runs on the NRC workbook; input bytes unchanged. |
| T21 | Structure and code rules C1, C2, C4, C5. | 03 | T20 | S | A renamed tab, a bad answer type and a type 2 without scores are each found. |
| T22 | Marks rules C6–C9. | 03 | T20 | S | NRC_2027_28_001: total 85 and "Lead time" 0 marks / 1,2,3 found. |
| T23 | Field rules C10–C15, reusing the `find` side of T05–T18. | 03 | T20 + the rules | S | Each rule has a failing and a passing test sheet. |
| T24 | Missed lines C16–C18: diff against the generated version, incomplete standard blocks, other clients' names. | 03 | T20 | M | Deleting a row, changing a mark, removing Ref 2's "Contact person" are each found. |
| T25 | C3 categories cross-check, and the pre-write check: generation runs all checks and lists failing sheets as NOT WRITTEN. | 02 G11, 03 | T21–T24 | S | A freshly generated ERCS batch passes with zero errors. |
| T26 | AI read (optional): meaning problems as model-labelled warnings, through the privacy layer. | 03 | T20 | M | An "attach" question coded as text is flagged; `--no-model` makes no call. |

## Phase 3: category refinement

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T27 | Read draft lists: number, description, group headers in either column or merged; CSV too. | 01 | | S | The Red Cross draft reads as 74 categories in 9 groups. |
| T28 | Code proposals: Title Case, `Repeated` duplicates, overlap candidates by similarity. | 01 R1–R3 | T27 | S | Red Cross #52/#59 marked `Repeated`; #11/#19 among overlap candidates. |
| T29 | Model proposals: renames, typo fixes, merges, separations, questions for the client, suggested additions, sub-categories, Eligibility, Gate, Applies To; schema-checked. | 01 R1–R8 | T27 | L | Proposals arrive for every draft row with reasons; nothing leaves the machine un-anonymised. |
| T30 | Traceability enforcement and `revised.xlsx` (Draft, Revised, Summary tabs). | 01 R10 | T28, T29 | M | Every draft row has a status; a dropped row stops the write. |
| T31 | Apply the review: decisions, client comments as proposals, Final Categories tab, categories table out. | 01 Step B | T30 | M | A reviewed file yields a final list and a categories CSV that `generate` accepts. |
| T32 | Evaluate on NRC history (draft → revised → final). | 01 | T30 | S | Report of the team's changes the tool proposed, and its extra proposals. |
| T33 | Run on the Red Cross draft and hand the revised list to the team. | 01 | T30 | S | `revised.xlsx` delivered. |

## Phase 3b: learning from the bank (spec 04)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T36 | Group rewordings of a question (similarity threshold, a setting); never merge questions confirmed separately. | 04 L1 | | M | The bank's 69 scored Yes/No questions form about 53 groups; the warranty and "company / organization" variants group. |
| T37 | Reuse a group's confirmed answer for a new rewording, as `drafted (from similar)`. | 04 L2 | T36 | S | A reworded question gets the verdict and names of its confirmed twin, marked as a draft. |
| T38 | Chunked model calls (at most 40 per call, per client) for names and verdicts; a failed chunk falls back to rules alone. | 04 L5 | | S | 100 new questions make 3 calls; one failing call leaves the other chunks' answers. |
| T39 | `learn` command (and `ingest --learn`): seed and fill the glossary, judge every scored Yes/No question, for new questions only. | 04 L3, L4 | T36–T38 | M | A second run with no new history makes no call. |
| T40 | Review workbook of drafts, one line per group; `learn --review` applies the answers as `confirmed`. | 04 L6 | T39 | M | Answering `ok` or a correction on a line confirms every question in its group. |
| T41 | `generate` asks only about questions new to the batch, chunked, and lists those drafts in the QA report. | 04 L7 | T38, T39 | S | After `learn`, an ERCS build makes no model call for questions the bank already holds. |

## Phase 5: research - bank first, then the web (spec 05)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T42 | Research table behind the storage driver: finding (kind, topic, country, proposal, sources, status); confirmed findings never re-asked. | 05 | | S | Findings round-trip; a confirmed one survives a re-run. |
| T43 | Gemini with Google Search: a grounded request (JSON asked in the prompt) returning the pages used; findings without a source dropped. | 05 | | M | A faked grounded reply yields findings with titles and links; one with no source is dropped. |
| T44 | Bank-first lookups for the four kinds: closest past categories (any client), past technical questions, past eligibility/technical documents, glossary names. | 05 K1-K4 | T42 | M | A category close to a past one gets its name and source with no web call. |
| T45 | `research` command and its review workbook; `--country` (default from the code prefix). | 05 | T42-T44 | M | A second run makes no web call; the review lists only new findings. |
| T46 | Refine shows K1 and K3 (technical name, usual split, suggested eligibility, sources). | 05 K1, K3 | T45 | S | Red Cross: water purification chemicals named with sources; works split by trade shown. |
| T47 | Generate uses K2 and K3 for categories the bank has no source for; K4 names unknown documents. | 05 K2-K4 | T45 | M | A category with no past sheet gets researched questions, NEW, 0 marks, sources on the rows. |
| T48 | Client-neutral check: no client name or client-specific rule in prompts or code paths; same run for ET and KE. | 05 | T46, T47 | S | A test scans the prompts for client names; ET and KE runs differ only by country. |

## Phase 4: wrap-up

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T34 | Regenerate the ERCS 2026 batch end to end and run the check on it. | 02, 03 | Phases 0–2 | S | Batch written in the new template; check passes; numbers from the spec 02 acceptance list reported. |
| T35 | Update README and SPEC.md for the new template, the three flows and the learned files. | all | T34 | S | README commands match the CLI. |

## Phase 6: web app (spec 06)

Agreed 2026-10-29: local first, review in the browser, two flows + bank and
categories pages, a monorepo with each party in its own package. The move
and the service layer land first, so the CLI and the API share every step.

### 6a. Monorepo and core

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T49 | Move to the monorepo layout with `git mv`: `tendersure/` → `packages/core/src/tendersure/`, `tests/` → `packages/core/tests/`; core `pyproject.toml`; root dev `pyproject.toml`, `Makefile`, `.gitignore`; `requirements.txt` and `run_ingest.py` go. `cli.py` stays in core for now. | 06 M4 | | S | `pip install -e packages/core` then `make test` passes all 129 tests (corpus included); `git log --follow` shows history on moved files. |
| T50 | `Paths` in core (data, cache, outbox) replacing the cwd-relative literals; no `.env` read at import time. | 06 M3 | T49 | S | Tests run with `Paths` pointing at a temp folder and write nothing under the repo. |
| T51 | `tendersure.flows`: ingest, refine (propose, apply) and generate orchestration out of `cli.py`; progress callback (incl. the 5 fallback prints), result dataclasses, errors instead of `sys.exit`. | 06 W1 | T50 | L | An ERCS generate and a Red Cross refine give identical workbooks before and after. |
| T52 | Generation takes `proposals` and `answers` in memory; reading a QA file fills the same dicts. | 06 W1 | T51 | S | Answers from a dict and from the QA file give the same workbook. |
| T53 | `apps/cli` (`tendersure_cli`, console script `tendersure`): argparse and printing only, over `flows`; `cli.py` leaves core. | 06 W1, M1 | T51 | M | Every README command runs as `tendersure …` with the same output; core has no argparse import. |
| T54 | Import contracts (import-linter): core imports no Flask/argparse and no app; apps import only `tendersure.flows`; only apps import flows; api and cli never import each other (added with the API in T55). Wired into `make test`. | 06 M1 | T53 | S | A deliberate forbidden import fails `make test`. |

### 6b. API

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T55 | `apps/api` (`tendersure_api`): flask-openapi3 app, `tendersure-api` entry, serves `apps/web/dist`, 127.0.0.1 only, Host check, `X-TenderSure` header on writes, JSON errors; its own tests. | 06 W2 | T54 | S | Test client: POST without the header → 403; foreign Host → 403. |
| T56 | Pydantic schemas for Draft, Line, Category, Item, job and run results, mapped from core's dataclasses. | 06 M2 | T55 | S | Mapping round-trip of every type is lossless. |
| T57 | Workspaces: exercises (create, list, read), upload / download with sanitised names, `workspaces/` git-ignored. | 06 W3 | T55 | S | Upload then download gives the same bytes; `../x.xlsx` is refused. |
| T58 | Job runner: one worker thread, queue, progress lines, persisted status, `interrupted` after restart. | 06 W4 | T55 | M | A test job's progress is readable while it runs and after a simulated restart. |
| T59 | Settings and bank endpoints: model status (no key), bank status, ingest job, report downloads. | 06 S1, S2 | T56–T58 | S | Ingesting the synthetic corpus via the API gives the same bank as the CLI. |
| T60 | Refinement endpoints: upload and parse draft, propose job, read / patch revised lines, Excel export / import, apply. | 06 S3 | T56–T58 | M | API test: synthetic draft → propose → decisions → apply gives the CLI's final list. |
| T61 | Categories endpoints: read, patch rows with the T19 check per row, import from final list or CSV, export CSV. | 06 S4 | T56, T57 | S | A mismatched gate row comes back with its problem; the exported CSV loads in `generate`. |
| T62 | Generation endpoints: run job, runs list, decisions read / patch, apply-answers job, downloads. | 06 S5 | T52, T56–T58 | M | API test: generate, answer one Q and one M item, apply; the second run's QA shows them applied. |

### 6c. Contract and web

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T63 | `packages/api-client`: `make contract` exports openapi.json and generates TS types (openapi-typescript) + a typed fetch client that sends the header; freshness check in `make test`. | 06 M2 | T59–T62 | S | Changing a schema without `make contract` fails `make test`. |
| T64 | `apps/web`: Vite + React + TS, TanStack Router (file routes) + Query + Table, Tailwind, ESLint rule that `/api` is reached only via api-client; pnpm workspace; `make dev` runs both servers. | 06 M1, M4 | T63 | S | `make dev` shows the home page reading settings; `make build` + `tendersure-api` serves the build. |
| T65 | App shell and Home: exercises list and create, bank and model status, job progress component. | 06 S1 | T64 | S | Create an exercise from the browser; a running job shows its lines. |
| T66 | Bank page: add workbooks, ingest summary, report downloads. | 06 S2 | T65 | S | Ingest from the browser matches the CLI summary. |
| T67 | Refinement page: upload draft, preview, run, review table (filters, decision, comment, edit wording), Excel round-trip, apply and final list. | 06 S3 | T60, T65 | L | The Red Cross draft goes from upload to categories table in the browser. |
| T68 | Categories page: editable table, inline gate check, import / export. | 06 S4 | T61, T65 | M | A gate mismatch shows on its row and clears with the edit that fixes it. |
| T69 | Generation page: settings, run with progress, results and downloads, Decisions table with per-kind controls that write the spec 02 answer grammar, apply, earlier runs. | 06 S5 | T62, T65 | L | The ERCS batch is generated, reviewed and re-applied from the browser. |
| T70 | Vitest for the answer-grammar builders; one end-to-end API test of both flows on synthetic data. | 06 | T67, T69 | S | `make test` runs pytest (3 packages), import contracts, contract check and Vitest; all pass. |
| T71 | README (install, `make dev`, `tendersure …`, `tendersure-api`), SPEC.md pointer to spec 05, a README per package saying what it owns and may import. | 06 | T69 | S | A fresh clone reaches the home page by following the README. |

Suggested slice: T49 → T54 (the monorepo, with the CLI working as before),
then T55–T60 → T63 → T64 → T67 (refinement end to end in the browser; it is
the smaller flow), then T61, T62, T68, T69.

## 7. The client's brief and requirement columns (spec 07)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T72 | `read_draft`: sub-category continuation rows; Licence / HR / Technical columns by header; an "Individual Consultant" cell sets Applies To and the gate; a document-like sub-category moves to licence; item splitting (commas, slashes, years, bracketed lists). | 07 R1, R2 | - | S | A synthetic PSI-shaped draft reads into the expected fields. |
| T73 | Refinement carries the fields: Line fields, Revised tab columns (written and read back), merges join items, final list into new Category fields `licence` / `hr` / `technical` in the categories CSV; API schemas, `make contract`, Categories page columns. | 07 R3 | T72 | M | Draft -> refine -> apply -> CSV -> load is lossless; the categories page edits them. |
| T74 | Generation rules: licence -> Eligibility rows; HR roles replace skeleton role rows keeping marks, extras 0, leftovers removed and listed; technical items replace or add upload rows; notes; fields fill `requirements`. | 07 R4 | T73 | M | The R4 acceptance sheet: 5, 5, 0 marks, total 100, check passes. |
| T75 | Brief input: `--brief` on refine / generate / check; flows parameters; stored on the exercise and in run settings; text box on the Refinement and Generation pages. | 07 B1 | - | M | A brief typed in the browser appears in the run's settings and its prompts. |
| T76 | Brief in the prompts of refine, selectors, reviewer, check_ai (first, with the priority wording); `from_brief` in their schemas and labelled in the QA Decisions tab; rule-conflict notes; offline note in the QA Summary. | 07 B2-B6 | T75 | M | Logged prompts of the four steps start with the brief; naming / yes-no / research prompts do not contain it. |

Suggested order: T72 -> T73 -> T74 (PSI's columns work offline), then T75 -> T76.

## 8. The client's benchmark criteria (spec 08)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T77 | `benchmark` module: load a workbook as the exercise client's tabs (any code or client on them ignored), check each (total 100, structure errors), report usable / refused with reasons. | 08 B1 | - | S | The synthetic three-tab benchmark: one usable, the 98 tab refused with its total, the coded tab usable under the exercise's client. |
| T78 | The map: propose group (+ kind) -> tab by shared words, else by content, one line per kind in a mixed group; read / write the CSV, keeping corrected lines. | 08 B2 | T77 | S | The proposal matches by word, by content, and splits a mixed group. |
| T79 | Generation from a mapped tab: `Generator.plan` takes the skeleton, rows not flagged as category-specific; flows `benchmark` / `benchmark_map` settings; refused or unmapped -> bank with a note; QA names the tab; CLI `--benchmark`, `--benchmark-map`. | 08 B3 | T78 | M | A mapped category's section marks equal the tab's, HR roles keep the tab's role marks, the check passes. |
| T80 | API and web: benchmark upload per exercise, its tab report, the map table (proposed, editable), generation runs use them. | 08 B1, B2 | T79 | M | Upload, correct one map line and generate in the browser; the run's sheets come from the mapped tabs. |

Suggested order: T77 -> T78 -> T79 (works from the CLI), then T80.

## 9. Split and merge in the review (spec 09)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T81 | Line fields `split_into`, `merge_with`, `suggested_split`, `suggested_merge`; `apply_review` applies split and merge (pieces keep the line's rows and items, sub-category pieces have none; folded lines listed), bad ones are problems that change nothing; traceability holds. | 09 S1 | - | M | The S1 acceptance cases pass. |
| T82 | Rule suggestions: merge from overlaps, split from the draft's sub-categories and from two procurement heads joined by 'and'. | 09 S2 | T81 | S | The suggested split / merge cases pass; none is applied unless chosen. |
| T83 | Workbook: Split into, Merge with #, Suggested split / merge columns written and read back; Decision takes split / merge; Summary explains them. | 09 S3 | T81 | S | Round trip keeps both; an answered workbook applies like the browser. |
| T84 | API and web: line change takes split_into / merge_with; Review table shows #, split editor (pieces, add / remove, from the suggestion), merge input with the overlap button; `make contract`. | 09 S4 | T81-T83 | M | A split and a merge done in the browser give the same final list as the workbook. |

## 12. Client rules, country-first sources, PSI's comments (spec 12)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T85 | `client_rules` learned table and the four actions (remove, replace, reword, limit) with group scope; applied first in finalise, marks kept in the section. | 12 C | - | M | The C acceptance cases pass. |
| T86 | API + web: Client rules card on the Generation page (table, add / edit / delete). | 12 C4 | T85 | S | A rule added in the browser changes the next run. |
| T87 | Country-first sources: skeleton and suggestions prefer the target country; a foreign source is noted. | 12 S | - | S | Ethiopian client starts from the Ethiopian sheet. |
| T88 | Draft-only sub-categories at proposal and apply; the model's shown as a suggestion. | 12 R1 | - | S | A line with no draft sub-categories applies none. |
| T89 | Technical items replace the Technical section; equipment one question per item. | 12 R2 | - | S | Exactly the items, marks kept. |
| T90 | Partnership; Organization type options on every sheet; Fayda ID / national ID for individuals on gated sheets. | 12 R3, R4 | - | S | As in the acceptance. |

## 13. One shared server (spec 13)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T91 | Shared-write lock in core (thread + file lock on `data/.lock`) around every read-change-write of the bank and learned files: client rules, glossary drafts, research, ingest; CLI writers take it too; SQLite busy timeout. | 13 H3 | - | M | Two threads and two processes saving different clients' rules at once: both kept. |
| T92 | Identity: user from `Tailscale-User-Login` / `-Name` on loopback requests only, else `local`; `TENDERSURE_HOSTS` adds allowed hosts; `GET /api/me`; jobs record who started them; `data/audit.jsonl` line on every save; outbox records the user; name in the UI header and on jobs; `make contract`. | 13 H2 | - | M | Headers from a non-loopback address are ignored; a save writes one audit line with the user. |
| T93 | Production serving: waitress (one process, threads), `tendersure-api --serve`, systemd unit and `tailscale serve` config under `deploy/`. | 13 H1, H5 | - | S | The unit starts the app on 127.0.0.1; health answers through `tailscale serve`. |
| T94 | `scripts/provision` wizard: VM, tailnet, firewall with no public ports, service user, home, `.env` (600). | 13 H1, H5 | T93 | S | A fresh VM serves the app on the tailnet following only the wizard. |
| T95 | `make deploy`: wait for idle jobs (`--force`), check out, install, build web, restart, health check. | 13 H5 | T93 | S | A deploy during a job waits; with `--force` the job shows `interrupted`. |
| T96 | Backups: nightly timer, SQLite online backup, restic to object storage, 14 daily / 8 weekly; `scripts/restore`; `scripts/pull-snapshot` for a laptop sandbox. | 13 H4, H6 | T94 | M | A restore into an empty home shows the same Home numbers and exercises. |
| T97 | Retention pruning (outbox and jobs 90 days, cached answers 180) on the nightly timer; move in: seed the server home from this machine. | 13 H7, H8 | T96 | S | Files older than the limits are gone, younger ones kept; the server's Home matches this machine's. |

Suggested order: T91 -> T92 (both safe on laptops too), then T93 -> T94 -> T95 -> T96 -> T97.

## 14. PSI's Version 2 comments (spec 14)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T98 | Built-in audience rules when the learned table has no answer; "Full name (as per Fayda ID)" for individuals on gated sheets. | 14 A | - | S | The A acceptance cases pass. |
| T99 | Technical items carry marks (`[n]`); the difference rebalanced over the other sections; items worded as questions kept as written. | 14 B | - | S | 10 / 3 / 2 kept, sheet at 100. |
| T100 | Client-rule action `marks` for a sub-section, run last in finalise, other sections rebalanced. API and web accept it. | 14 C | - | S | Ref 1 / 2 / 3 at 15 / 15 / 10, sheet at 100. |
| T101 | Writer sets question row heights from wrapped text. | 14 D | - | S | Long questions get tall rows. |

## 15. The client's rules as checks (spec 15)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T102 | `rule_checks`: client rules, categories-table items and built-in audiences checked on a workbook; wired into `check` (CLI, Check an edited copy, run check). | 15 | T85, T98, T99, T100 | S | PSI run 24: 0 errors; the acceptance edits each fail with their rule. |

## 16. What PSI taught us (spec 16)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T103 | Every run checks the client's rules and category items on the sheets written; broken ones are F- cards; result and Ready panel count them. | 16 A | T102 | S | A run says its rules hold; a broken one is a card. |
| T104 | Client profile: form <-> rules of its shapes, country defaults, API, web card before the first build. | 16 B | T85 | M | PSI's rules read back as a profile; saving keeps other rules. |
| T105 | Rules proposed from the benchmark workbook and from an edited copy's repeated changes; Add from the rules card and the check result. | 16 C | T104 | M | PSI's samples propose Ref 15/15/10 and PO; three removed IBAN rows propose remove IBAN. |
| T106 | Marks cards named by their sheets; a client marks rule wins over an answer, and its status says so. | 16 D | - | S | Same ID when marks move; status names the rule. |
| T107 | Technical items edited as a list with a marks box each. | 16 E | T99 | S | Saved with 3 reads back as "... [3]". |
| T108 | Sheet preview: API reads a run's sheet back; web card with merged sections and wrapped questions. | 16 F | - | S | The preview lists the sheet's rows with marks. |
| T109 | Warn when a gated build has no learned audience. | 16 G | T98 | S | The note is on the sheet and in the log. |

## 17. PSI's Version 3 comments (spec 17)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T110 | Client-rule action `add` (wording, `[n]` marks, answer type from the wording, section created when missing), `#n` category scope, never limited, held out of carried marks answers, `RULE-ADD` check. | 17 A | T85, T102 | M | The A acceptance cases pass. |
| T111 | Trade License named Trade License / TL; replace rules change short names and their acronyms. | 17 B | - | S | TL whatever the glossary says. |
| T112 | `=SUM()` sub-totals by default (CLI `--values`); the check reads a formula total from the rows. | 17 C | - | S | Default build writes formulas; check passes. |
| T113 | Import the client's approved category list (.xlsx), keeping the team's settings by description. | 17 D | - | S | PSI's list: 77 categories, gates and engineering items kept. |

## 18. A draft in several tabs, and the client's previous list (spec 18)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T114 | Reader: `Serial` / `S.No` headers; hidden tabs skipped (default = first visible tab with a header); short un-numbered rows before a numbered row are groups; a leading old number dropped and recorded `Renamed`. | 18 A | - | S | Amref goods tab: 54 categories, `Engineering` a group, #37–39 renamed. |
| T115 | Several tabs as one draft: repeatable `--sheet`, the tab's name as the first group, unique IDs across tabs; API takes a list of tabs; web tab checkboxes without hidden tabs. | 18 B | T114 | M | Both Amref tabs: 94 categories, consultancy #16 `Repeated` of #7. |
| T116 | Sub-category lines: dash/bullet rows (and the row above them) under a numbered category become its sub-categories; unplaced rows carry the note. Model prompt allows `Separated` by related sub-categories. | 18 C | T114 | S | Consultancy #1 has 5 sub-categories; no line from rows 5–9. |
| T117 | Previous list reader: draft layout or results layout (code headers, merged headers, counts dropped, supplier rows never kept); test that no supplier name reaches outputs, log or prompt. | 18 D | T114 | M | 98 names; no supplier name anywhere. |
| T118 | Previous list in refinement: `Previous list` column, unmatched names proposed `Additional`, model prompt section and "covered by"; CLI `--previous`, API field, web upload. | 18 D | T115, T117 | M | Mobile toilets and taxi proposed; psychometric matched across tabs. |

## Suggested first slice

T01 → T02 → T03 → T06 → T07: the new template plus the two fixes the team
will notice first (marks in two columns, unbroken blocks), then regenerate
ERCS for an early look.

## 19. What Amref's first proposal showed (spec 19)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T119 | Eligibility applied only from the client's own text (draft rows, previous names); the model's wording suggests. | 19 A | - | S | The IATA rename suggests, never applies. |
| T120 | Previous names keep their tab; consultancy tabs open additions to individuals; reason names the tab; the previous list is read again on each proposal (API keeps `tab_of`). | 19 B | T118 | S | Statisticians proposed with Individual Consultant. |
| T121 | Previous matching: supply-only and repair-only wording never match. | 19 C | T118 | S | Camera repair proposed beside camera supply. |
| T122 | Suggested split keeps a sub-category that already starts with a procurement word. | 19 D | - | S | No "Provision of Provision of". |
| T123 | `title_case` keeps i.e., e.g., etc. lower case, in brackets too. | 19 E | - | S | The E case passes. |

## 20. The team server on Render (spec 20)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T124 | `tendersure-api --serve`: waitress on 0.0.0.0:$PORT, refuses without a 16+ character `TENDERSURE_PASSWORD`; HTTP Basic on every request but `/api/health`; same-origin check and `X-TenderSure` kept; `TENDERSURE_WEB_DIST`. | 20 R1, R2 | - | S | Done: the team-server tests in apps/api/tests/test_app.py pass. |
| T125 | `Dockerfile` (web build, then the Python parties), `.dockerignore` keeping client data out, `render.yaml` (Frankfurt, disk at /app/home, manual deploys, generated password). | 20 R1, R3 | T124 | S | Done: the image builds and answers /api/health; the password guards the rest. |
| T126 | Move in: copy `data/`, `workspaces/`, `.cache/` onto the disk; the server's Home shows this machine's numbers. | 20 R3 | T125 | S | Same Home numbers and exercises on Render. |

## 21. The category list, shaped by the team's comments (spec 21)

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T127 | Core comments module from the prototype: actions incl. merge, instructions with the lines each targets, ask again for missing ones, the three flags, sources (draft, brief, approved lists); tests on recorded answers. | 21 B, C | - | M | Done: tendersure/catlist.py, tests in test_catlist.py. |
| T128 | The list store: list from the draft as written, versions, apply ticked changes, undo latest; client workbook and categories table from it. | 21 A, D, G | T127 | M | Done: list_api.py storage; versions, undo, outputs tested in test_list.py. |
| T129 | API: list, comment (job), decide, undo, log, check against a list; one waiting comment per exercise; re-read when the list changed. | 21 B, D, F | T128 | M | Done: list_api.py routes; one waiting comment and re-read tested. |
| T130 | Web: the Refinement page as layout A: the list as the client's document, the comment margin with instructions, changes, sources and flags, Apply/Reject/Undo, the check panel, downloads. | 21 A–D, F, G | T129 | M | Done: the Refinement page (components/list/); Amref's draft renders. |
| T131 | Remove the AI proposal, Decisions table, previous-list card and internal review workbook (UI, API, core); move the prototype to a throwaway branch. | 21 H | T130 | S | Partly: UI, API and prototype removed; the old core refinement and CLI `refine` remain. |
| T132 | Standing comments ("always, for this client"): kept per client, offered in order on the next exercise. | 21 E | T130 | M | A kept comment is offered on a new exercise of the client. |
| T133 | Acceptance run on Amref from its draft as written, recorded in the spec. | 21 Acceptance | T130 | S | Done: 61 categories, same group 55/62, 6 missing, 5 extra (spec 21, Acceptance run). |
