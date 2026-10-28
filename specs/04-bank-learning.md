# Spec 04: Learning from the bank

Prepares, once per batch of history, the facts every later build reuses, so
that loading years of past sheets does not turn into a model call and a
review line per question during each `generate`.

## Why

Two facts are learned per question today, both during `generate`:

- its **Archive Short Name and Acronym** (`data/glossary.csv`, spec 02 G8);
- for a scored Yes/No question, **whether Yes is the good answer**
  (`data/yesno_polarity.csv`, spec 02 G10).

That works for one batch, but not for history at scale:

- the same question reworded counts as a new question: the current bank's 69
  scored Yes/No questions are 53 once rewordings are grouped ("Does your
  company / organization have the necessary technology…");
- a large intake would be one oversized model call;
- the team would review drafts scattered through CSV files.

## The step

`python -m tendersure learn`, run after `ingest` (or `ingest --learn`).
It reads the bank's usable sheets only (graded clean or correctable) and
handles only questions it has not seen before, so a re-run after adding a
year of history costs only that year's new questions.

**L1. Group rewordings.** Questions whose normalised text is at least 85%
similar are one group (the threshold is a setting). A group shares one
glossary entry and one Yes/No verdict. Grouping proposes; it never merges
two questions the team has confirmed separately.

**L2. Reuse before asking.** A new question in a group that has a confirmed
answer takes it, marked `drafted (from similar)`: a rewording can flip the
meaning ("have you had *no* contracts terminated"), so it stays a draft for
the team.

**L3. Glossary.** Seed from the bank as today (spec 02 G8: the client's own
names first, then other clients'). Questions needing names with none are
drafted: Short Name by the model, Acronym by the letter rule.

**L4. Yes/No.** Every scored Yes/No question without a verdict is judged:
by the model, else the keyword rule.

**L5. Chunked calls.** Model questions go in calls of at most 40 (a
setting), one client at a time for anonymisation, each anonymised and logged
like every call. A failed call falls back to the rules for its chunk only.

**L6. One review.** The step writes `reports/learned_review.xlsx` with only
the drafts, one line per group: the question (and its rewordings), the
proposal, the source (model, rules, similar question). The team answers in a
Decision column (`ok`, or a correction); `learn --review <file>` applies the
answers and marks them `confirmed`. Confirmed rows are never re-asked or
overwritten by a re-seed.

**L7. Generate reuses.** `generate` reads the learned files and asks the
model only about questions new to that batch (typically questions the AI
drafted for a category), chunked the same way, and lists those drafts in
the batch's QA report.

## Storage

The learned facts stay in plain CSV under `data/` (editable, diffable). The
review workbook is where the team works; the CSVs are the record. Moving
them into the bank database is left until the volume calls for it.

## Acceptance

- On the current bank: `learn` groups the 69 scored Yes/No questions into
  about 53, judges them in chunked calls, names every question that needs
  names, and writes one review workbook.
- A second `learn` with no new history makes no model call.
- Adding one workbook triggers calls only for its new questions.
- After the review is applied, `generate` for ERCS makes no `learn`-type
  model call for questions already in the bank.
