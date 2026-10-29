# Spec 03: Check after hand edits

Feedback item 6. Once a workbook is generated, the team edits it and sends
it on. Editing can break things; this flow finds what broke before the file
reaches the platform.

## Command

    tendersure check <edited.xlsx> [--against <generated.xlsx>]
        [--categories data/<client>_categories.csv] [--no-model] [--out report.xlsx]

- Reads the edited workbook only. **It never changes it.**
- Prints a summary per sheet and writes a report workbook: one line per
  problem with sheet, row, column, rule, severity, what was found and what
  was expected.
- Exit code is non-zero when any error is found, so it can gate an upload.
- Severity: **error** (the platform would reject it, or an agreed rule is
  broken) or **warning** (likely wrong, a person should look).

## Rules checked in code

**Structure and codes**
- C1. Header row present, 21 columns in the G1 order, labels as agreed.
- C2. Sheet code: row 2 reads `CODE: Description`; the code has the
  client prefix and a three-digit category number; the tab name equals the
  code; no two sheets share a code.
- C3. With `--categories`: each sheet's number and description match the
  categories table; a category with no sheet, or a sheet with no category,
  is reported.
- C4. Answer type is 1–5 on every question row, and each type has what it
  needs: types 2/3 have options and an equal number of numeric scores;
  type 5 has the Attachment or Compliance flag; types 1 and 4 have no options.
- C5. Kenya-only columns (Evaluation, Eval Options, Eval Options Scores,
  Verify Against) are empty (warning).

**Marks**
- C6. Every question row has a Marks value repeated in System or Compliance
  (spec 02, G4), and Marks = System + Compliance.
- C7. Each Sub Total equals the sum of its rows, for Marks, System and
  Compliance.
- C8. Total Marks = 100, and System total + Compliance total = 100.
- C9. Option scores (G9): for types 2 and 3, Marks is one of the scores and
  no score exceeds it.
- C10. Yes/No (G10): type and scores match the question's polarity; an
  unknown polarity is a warning.

**Agreed field rules**
- C11. Eligibility (G3): TRUE on every row of the Eligibility section and on
  no other; the section holds the Trade License.
- C12. Gate / Applies To (G7): exactly one Gate Question row when the
  category's `gate_question` is TRUE, none otherwise; every Applies To value
  is one of the gate's options; Applies To empty everywhere when there is no
  gate.
- C13. Short Name and Acronym (G8): present on every required row; acronyms
  unique within the sheet; a pair that differs from the glossary is a warning.
- C14. Sub-category question (G5): present with this category's
  sub-categories when it has any, absent otherwise.
- C15. Merged blocks (G6): no Section or Sub Section block split in two
  (warning).

**Missed lines**
- C16. With `--against`: rows removed, added, or changed since the
  generated version, listed by field. Removals and mark changes are
  warnings, so an intended edit is visible but does not fail the check.
- C17. Standard blocks complete: Declaration and Submission present; each
  Trade Reference block (Ref 1, Ref 2…) has Client Name, Contact person,
  Contact person details, Period of the contract and the evidence upload;
  each shareholder block has all its fields.
- C18. Another client's name in a question's text (e.g. "SNV" in an ERCS
  sheet).

## AI read (optional, `--no-model` skips it)

The model reads each edited sheet's rows (anonymised, as every model call)
and reports problems of meaning the rules above cannot see:

- an answer type that does not fit the question ("Please attach…" coded as
  text);
- a Yes/No question scored the wrong way round;
- a question that does not fit the category;
- wording errors that change meaning.

Its findings are always warnings, labelled as coming from the model, with the
row and a reason. It cannot raise an error.

## After the check

A workbook that passes can be ingested (`tendersure ingest`), so
the team's edits become history and later batches learn from them.

## Acceptance

- `NRC_2027_28_001` is reported with: total 85 (C8), "Lead time delivery
  period" with 0 marks and scores 1,2,3 (C9), and rows with Marks but blank
  System/Compliance (C6).
- A freshly generated ERCS batch passes with zero errors.
- Deleting one row, changing one mark, or retyping an answer type in a
  generated sheet each produces exactly the expected finding.
- The edited workbook's bytes are unchanged after the check.
