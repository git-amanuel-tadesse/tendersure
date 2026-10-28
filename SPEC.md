# TenderSure Criteria Generator — working spec

Context for whoever (or whatever) continues this build. Everything below was
derived from 66 real criteria sheets across three clients, not assumed.

---

## 1. The problem

TenderSure runs supplier prequalification exercises for client organisations
(ERCS/Ethiopian Red Cross, SNV, ILRI). Each exercise covers many procurement
categories — ERCS 2026–2029 has **58**. Each category needs its own criteria
sheet: a ~104-row questionnaire in Excel, uploaded to the platform, which
suppliers then answer.

**Current process:** find a similar sheet from a past exercise, copy it,
edit it to fit the new requirement, upload, fix whatever the system rejects,
re-upload. Takes **3–5 days per round**.

**Two failure modes:**

| Class | Caught by | Cost |
|---|---|---|
| Platform errors — marks ≠ 100, answer type out of range, option/score mismatch | Upload rejects it | Round-trips. Days. |
| Silent errors — valid file, wrong behaviour (e.g. "Please attach Trade Licence" coded as a text box) | Nobody | Reaches suppliers. Credibility. |

Because each new sheet descends from one arbitrary ancestor, **fixes never
converge** — someone corrects an error in one file, the next person copies a
different file that still has it. This is why the same two errors appear in
all six SNV sheets.

---

## 2. File format

One workbook per client exercise, one sheet per category.

- Row 2, col B: `CODE: Description` — e.g. `ERCS-ET-2026-002: Supply of Electronics/ Home Appliances`
- Row 3, col E: the literal word `Marks`
- Row 4: headers (detect the header row by finding `Section` in column B)
- Row 5+: questions, with `Sub Total` rows per section and `Total Marks` last

### Columns

| Col | Field | Notes |
|---|---|---|
| B | Section | set once per section, blank on following rows |
| C | Sub Section | e.g. `Shareholder 1`, `Ref 2`, `N/A` |
| D | Criteria | the question text |
| E | Marks | total for the row |
| F | System | portion scored by the system |
| G | Compliance | portion scored as compliance |
| H | Archive Short Name | document label, e.g. `TIN Certificate` |
| I | Acronym | e.g. `TIN`, `REF 1` |
| J | Answer Type | 1–5, see below |
| K | Mandatory | `True` or blank |
| L | Attachment | upload with **no** compliance marks |
| M | Compliance | upload that **carries** compliance marks |
| N | Options | comma-separated |
| O | Options Scores | comma-separated, must match N's count |
| P | Gate Question | **SNV sheets only** |
| Q | Applies To | **SNV sheets only** — e.g. `Private Limited Company, Share company` |

**Schema drift is real:** ERCS and ILRI sheets have 14 columns (no P/Q).
SNV has 16. Any parser must handle both.

### L vs M — resolved

These are **not** duplicates. Verified across all 66 sheets (only 1 row of
6,849 sets both):

- **L (Attachment)** — upload, column G empty. Conflict-of-interest form,
  bank request form, "if yes attach a copy".
- **M (Compliance)** — upload, column G carries marks. Registration
  certificates, TIN, trade references.

ERCS uses M exclusively (0 uses of L). SNV and ILRI use both, correctly.

### Answer types — INFERRED, NOT DOCUMENTED

⚠️ The TenderSure team knows these **verbally only**. There is no written
reference, which is a root cause of the silent errors. Confirm before relying
on it, and treat producing a written reference as a deliverable in itself.

| Code | Meaning | Supplier sees | Requires |
|---|---|---|---|
| 1 | Text | free-text box | — |
| 2 | Single select | pick one | options + scores |
| 3 | Multi select | pick several | options + scores |
| 4 | Yes/No | toggle | — |
| 5 | Attachment | file upload | L or M flag |

---

## 3. Corpus statistics (66 sheets, 6,849 question rows)

- **341 distinct questions** total
- **36 appear in all 66 sheets** (core)
- **51 appear in 50–95%** (common)
- **254 are category-specific**
- **84% of an average sheet** is made of questions appearing in ≥50% of others

So for a new sheet, roughly **87 of 104 questions already exist**. Only ~16%
is genuinely new. That ratio is the entire business case.

### Canonical section order (ERCS)

Declaration · Company Information · Registration and Statutory Requirements ·
Director/Shareholder Information · Business Integrity, Risk and Litigation ·
Sustainable Procurement (ESG) · Technical Specifications · Service Level
Requirements · Human Resource · Business Experience · Trade Reference ·
Financial Performance · Submission

Marks always total **100**, split between System and Compliance. The split
varies by category — e.g. ERCS-002 (goods) is 32/68 with a Technical
Specifications section worth 15; a consultancy category drops Technical
Specifications and weights Trade Reference higher (up to 55 in SNV-016).

### Category codes map to the advert

The number in the sheet code **is** the category number in the client's
published prequalification notice. `ERCS-ET-2026-019` = category 19,
"Supply of Assorted Pharmaceuticals", description copied verbatim.

The notice also groups categories (GENERAL, ICT, MEDICAL/PHARMACEUTICAL &
RELIEF, COMMUNICATION, HOSPITALITY, TRANSPORTATION & TRANSIT, CONSULTANCY &
PROFESSIONAL SERVICES, three ENGINEERING groups) and lists sub-categories.
That grouping is a better retrieval signal than text similarity, because the
client already classified their own categories.

**12 of ERCS's 58 categories have completed sheets.** Those are labelled
training examples: category description in, questionnaire out.

⚠️ Notices get revised. The ERCS advert was reissued with the deadline moved
(Sept 4 → Sept 10) and category 46's description expanded. Treat the category
list as versioned input.

---

## 4. Architecture

**Rule: the model selects, the code assembles.** Never let a model write
Excel rows directly.

- **Model's job** — read the category description and the user's prompt,
  decide which banked questions apply, flag gaps, draft genuinely new
  questions.
- **Code's job** — pull selected questions with their fields already
  attached, lay out sections and subtotals, enforce total = 100, write the
  file.

Answer type, options, scores and upload flags are **properties the question
already carries from the corpus**. If a model regenerates them per run, you
have reinvented the error class you are trying to kill.

### Storage

A plain relational table is the primary store — filtering by client,
category, section and supplier type needs exact answers, which vector search
is bad at. Embeddings sit **beside** it for similarity (near-duplicate
wording, "has anything like this been asked?"). Filter first, then rank.

### Retrieval

Match at the **requirement level**, not the question level. Embed the
category description, find the most similar past categories, pull their whole
questionnaires and merge. That yields a coherent set someone actually used,
rather than individually-plausible questions that do not hang together.

### Pipeline

1. **Parse** every workbook → rows. Deterministic.
2. **Validate before admission.** Compute validation findings and a trust grade
   before admitting a sheet. Sheets graded *clean* or *correctable* may enter
   the reusable bank. Sheets graded *quarantined* or *superseded* must not
   contribute questions to retrieval or generation; retain them only in a
   clearly marked quarantine/audit area if needed for reports, provenance, or
   later re-evaluation. A validation or grading failure must not leave a
   partly admitted workbook or its sheets in the reusable bank. When a source
   workbook changes, validate the new version before replacing its previously
   admitted version.
3. **Resolve conflicts.** Where the corpus disagrees on a structural field,
   a human rules. ~46 decisions for the current corpus.
4. **Store** with tags: client, country, category code and group, section,
   supplier type (from `Applies To`), purpose (scored / compliance /
   declaration), frequency, last used.
5. **Generate** from category + prompt.
6. **Human QA.** Tag each output row by origin — *reused unchanged* /
   *adapted* / *newly drafted* — so she reads ~20 rows properly instead of
   104.
7. **Feed edits back** as new variants. The bank improves from use.

### Non-negotiables for adoption

- Output is **Excel in their existing format**. No new application to learn.
- The bank **suggests, never enforces**. She can override every warning.
- **Batch over speed.** Checking all 58 in one pass matters more than
  per-file latency.
- Marks are a **client decision**. Suggest a split from similar categories,
  show where it came from, make her confirm. Code enforces the 100 total.

---

## 5. Scope boundary

**In:** everything before the file reaches the platform, and the question
bank behind it.

**Out:** the platform itself. There is a live rendering bug in the Kenyan
client's Trade Reference section (Ref 2 and Ref 3 render twice; marks read
17/19/19/19 = 74 against a stated subtotal of 55). **Mention it once, do not
fix it.** That is their dev team's scope.

**Separate project:** application review. Kal's team reviews ~700 submissions
at ~10 min each (~117 hours). Worth automating, but it is its own use case
with its own risks — any AI output there must be *evidence* (here is the
certificate, here is the expiry, page reference) and never a verdict, because
suppliers appeal and the platform's whole reputation rests on auditability.

---

## 6. Open questions

1. What errors does the upload **actually** reject? Ask the team to save real
   error messages from a failed upload — those messages are the platform's
   validation rules, written down by the platform itself.
2. Is the written answer-type mapping documented anywhere, or is it only
   verbal? (Current evidence: verbal.)
3. One workbook with N sheets, or N separate files, for platform import?
4. Is 3–5 days per *round* or per *category*?
5. How many upload round-trips does a typical file take before it passes?
6. Can a question be both scored and compliance, or is it always one?
7. Data permission: the corpus spans three clients' confidential criteria.
   Get explicit sign-off before assembling a cross-client bank, and agree
   ownership of the result now, while everyone is happy.

---

## 7. Current state of this code

All pipeline stages exist: parse, validate, bank, category extraction,
retrieval, generation, Excel writer, reports, evaluation, optional model step.
See README for commands.

### Corpus numbers (corrected 2026-10-15)

The first version of the parser read four `Sub Total` labels that had been
typed into the Criteria column as questions. With that fixed:

| | Before | Now |
|---|---|---|
| Question rows | 6,849 | **6,845** |
| Findings | 217 (13 rules) | **255 (19 rules)** |
| Bank entries | 341 | **340** (core 36, common 51, category 253) |
| Conflicts | 46 | 46 |
| `AT-MISSING` | 8 | **4** — the other 4 were the misread labels |
| `SUM-MISMATCH` | 6 | **2** |

### Headline findings from the current corpus

- `AT-TEXT-SAYS-ATTACH` (14): "Please attach Renewed Business/Trade License"
  and "Please attach Valid ID/Passport" are coded type 1 (text box) in **all
  six SNV sheets**. Mandatory, compliance-flagged, and the supplier is asked
  to *type* a licence.
- `AT-ASKS-TEXT` (5): "Insert Tin number" coded type 5 (upload) in four SNV
  sheets — while the identical question one block above is type 1.
- `OPT-MAX-MARKS` (28, new): on 534 of 562 scored select questions the best
  option scores exactly the row's marks. The 28 exceptions are errors: ILRI
  "declared in default of a contract" is worth 3 but its best answer scores 2
  (no supplier can reach 100); SNV litigation questions score up to 3–5 on
  rows worth 0.
- `SUM-ROWS` (9, new): question rows do not add up to the subtotal/total the
  sheet states. SNV-015, ILRI-060 and ILRI-061 award up to **105**; ILRI-064
  up to 103; SNV-022's rows add to 95 under a total of 100.
- `SUM-TOTAL-SPLIT` (3, new): ILRI-073/081/082 total 100 but System +
  Compliance = 92 / 97 / 101.
- `SUM-LABEL-COLUMN` (4, new): ILRI-056–059 have "Sub Total" in the Criteria
  column.
- `SHEET-NEAR-DUP` (1, new): `Sheet1` in "Sample Criteria from Amanuel.xlsx"
  is a draft of SNV-016 (1 row differs) and has lost its client prefix
  (`ID-NO-CLIENT`). Both copies count toward bank frequencies.
- `AT-MISSING` (4), `SUM-SPLIT` (4), `TXT-PLACEHOLDER` (1), `OPT-COUNT` (1)
  as before.
- `AT-NO-UPLOAD-FLAG` (133) is common in ERCS, which never uses L. Whether
  the platform cares is unknown, so the generator does not "fix" it.

### Evaluation (leave-one-out against the sheets the team built)

Hide each sheet, generate from its description, compare.

| Client | Overlap with real sheet | Baseline: arbitrary same-client copy | Section marks exact |
|---|---|---|---|
| ERCS (with advert grouping) | 97% | 94% | 82% |
| SNV | 98% | 94% | 72% |
| ILRI | 92% | 87% | 74% |

Read-flags catch 62–87% of rows that should not be there (precision 35–68%).
About a third of missing rows appear among suggestions. Of the rows missing
in the hardest ERCS cases, roughly a third exist nowhere in the corpus (named
equipment, named engineering roles): that is the genuinely new part, and the
job of the optional model step or the reviewer.

### Decisions made while building

- **Subtotals are written as numbers, not formulas** (`--formulas` to switch).
  A file written by openpyxl has no cached formula results; if the importer
  reads values without recalculating, formulas would arrive blank.
- **Upload flag left alone when absent.** Only the two unambiguous answer-type
  errors are corrected automatically, plus any human ruling from the bank's
  Conflicts sheet. Every change is recorded on the row and in the QA report.
- **Inserted rows enter with 0 marks**; their donor's marks are kept as a
  weight and applied when a section split is set. After model edits a section
  keeps its previous total.
- **Privacy is enforced in code.** Every model request is anonymised inside
  the model client (client names, sheet codes, contact details; notices are
  reduced to the category table, never the raw PDF or narrative), scanned,
  and refused if anything identifying survives; what was sent is logged.
  Providers must not train on the data: Gemini direct requires a confirmed
  paid tier, OpenRouter requests require `data_collection: deny` + ZDR.
  `privacy-check` shows exactly what would be sent. This answers open
  question 7 only in part: cross-client use still needs the clients' consent.
- **Local caches are data and an input boundary.** Cache files may contain
  confidential workbook-derived content. Keep them out of version control,
  use a non-executable serialization format, and never load an unauthenticated
  cache through an executable deserializer such as Python `pickle`.
- **Model access is provider-agnostic** (OpenRouter or Gemini direct, `llm.py`); the default
  is Gemini 4 Argon. Swapping provider is a model-id change; replies are
  schema-checked in code, with one prompt-mode retry if a provider rejects or
  bends the structured-output request. The key lives in a git-ignored `.env`.
- **Category extraction has two engines.** The layout parser is exact for the
  ERCS notice; the model engine handles other layouts. Model output must
  match the PDF text word-for-word, and with `--engine both` disagreements are
  reported per category.
- **The review is a decision list, not a reading list.** Flagged rows repeat
  across categories (192 flagged rows in the ERCS batch are 29 questions; 127
  warnings are one rule), so the QA report leads with a Decisions tab of
  ~30 lines, and `--review` applies the answers to every sheet. This replaces
  step 6's "read ~20 rows per sheet" with "answer ~30 lines per batch".
- **No human QA of the history.** Step 3 ("a human rules on ~46 conflicts")
  does not scale to years of data. Instead: sheets are graded automatically
  and broken ones are never copied; within a client, a field 90%+ of uses
  agree on is the convention and outliers are corrected on reuse; a real
  disagreement is asked only when a batch uses it, and each answer is kept in
  `data/rulings.csv` for every later batch. On the sample corpus: 34 clean,
  20 fixed on reuse, 11 quarantined, 1 superseded; an ERCS batch raises no
  convention questions, an ILRI batch 12 (or one bulk "adopt majority").
- **The bank is the only source for a build.** `ingest` loads workbooks into
  `data/bank.sqlite` (sheets, rows, grades, and the workbook bytes for
  styling); unchanged files are skipped, changed ones replace their earlier
  version. `generate` reads only the bank, selects by category description,
  notice group and optional per-category requirements, rewords copied
  questions during the build (with a model), and writes the prequalification
  workbook. Removals and additions remain proposals (removals were right
  80% of the time in the first evaluation).
- **Fully automatic by default** (decided 2026-10-18). With a model
  configured, every build proposes, reviews and applies; the QA report is an
  audit trail with no open lines (marks stay the client's, client
  disagreements settle by majority and are saved as rulings). Third
  evaluation, after AI review: removals applied right 98% (50 of 51; the
  reviewer vetoed 5 of the proposer's 6 mistakes), overall 73% vs 60% for
  "keep everything"; 43% of bank additions match what the team used; about
  2 drafted questions per sheet (0 marks, unmeasurable against history).
- **The AI answers the QA file, not a person.** A second, independent model
  pass reviews every proposal with bank evidence; only agreed changes apply
  (a removal needs two signals), disagreements keep the client's wording and
  are recorded. The QA report is an audit trail. Second evaluation (with
  rewording, proposer alone): removals right 89% (up from 80%), but 54 of
  103 rewordings were questions the team removed - the reviewer may now turn
  those into removals when the bank agrees.
- **Rewording is a first-class action.** The first full evaluation (54
  sheets) showed that much of the team's real work on a copied question is
  rewording its subject ("tools for water pumps" -> "tools for kitchen
  equipment"), not removing it: about half of the model's "wrong" removals
  were questions the team had reworded. The model may now propose new wording
  for a flagged question; the question keeps its answer type, flags and marks.
  Trade reference, declaration and submission questions can never be proposed
  for removal. First evaluation (before rewording): removals right 80%,
  overall 77% vs 60% for "keep everything", additions 41% vs 28%.
- **The model proposes; it never applies.** Its output fills the Decisions
  tab (pre-filled removals on Q- lines, A- lines of additions and drafted
  questions) and is stored in the QA report so the review replays it
  exactly. Without a model, A- lines appear only where a past sheet on the
  same topic exists (ERCS batch: 12, e.g. catering gets ILRI's food-safety
  questions), and one N- line names the categories with no source (9).
- **A sheet that does not total 100 is not written**; it is listed as
  NOT WRITTEN with the section breakdown.

### New open questions

8. Does the importer recalculate formulas? (Decides `--formulas`.)
9. What does the platform do with an upload question that has neither L nor M?
10. Are the nine sheets whose rows exceed or fall short of 100 live, and do
    they really score suppliers out of 105 / 95?
