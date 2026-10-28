# Tasks

The specs split into small tasks, in build order. Each task is one reviewable
change with its own test. "Depends" lists tasks that must land first; tasks
with no dependency between them can be built in any order.

Size: **S** under half a day, **M** about a day, **L** two days or more.

**Done:** T01, T02, T03, T06, T07 (first slice, 2026-10-28); T04, T05. ERCS batch
regenerated: 21 columns, 0 rows with marks only in Marks (was 3,508), split
Sub Section blocks fixed. One one-row Technical section in ERCS-041 keeps the
blank Sub Section of the client's own sheet.

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

## Phase 4: wrap-up

| ID | Task | Spec | Depends | Size | Done when |
|---|---|---|---|---|---|
| T34 | Regenerate the ERCS 2026 batch end to end and run the check on it. | 02, 03 | Phases 0–2 | S | Batch written in the new template; check passes; numbers from the spec 02 acceptance list reported. |
| T35 | Update README and SPEC.md for the new template, the three flows and the learned files. | all | T34 | S | README commands match the CLI. |

## Suggested first slice

T01 → T02 → T03 → T06 → T07: the new template plus the two fixes the team
will notice first (marks in two columns, unbroken blocks), then regenerate
ERCS for an early look.
