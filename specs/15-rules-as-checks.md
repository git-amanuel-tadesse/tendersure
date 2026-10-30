# Spec 15: An edited copy is checked against the client's rules

The team or the client edits a final workbook by hand and sends it back.
*Check an edited copy* (spec 11 F2) catches broken template rules, such as a
total off 100 or a broken block. It also lists every change since generation.
It does not know what the client asked for, so "Ref 1 marks 15 → 20" is one
warning among many, and the sheet passes.

The rules that shape a build now also judge a workbook. Agreed 2026-10-30.

## R1. The client's rules, as checks

Each client rule (spec 12 C, spec 14 C) checks the sheets of its groups:

| action | error when |
|---|---|
| `remove` | a question contains *match* |
| `replace` | the term *match* is still there (skipped when *value* contains it) |
| `reword` | a question contains *match* but does not read *value* |
| `limit` | the section has more than *value* questions. Skipped for a Technical section whose category lists its own technical items (spec 12 R2). |
| `marks` | the sub-section's questions do not carry *value* marks in total |

- **Groups.** A sheet's group comes from the categories table. A sheet whose
  category is unknown is checked only by rules without groups.
- **Exemptions.** The Declaration and Submission are never checked, as they
  are never changed.
- **The message.** It gives the rule's note as the reason. Example: "Ref 1
  carries 20 marks; the client's rule says 15 (References: Ref 1 15 marks)".

## R2. The categories table's items, as checks

| item | error when |
|---|---|
| technical (spec 12 R2, spec 14 B) | an item has no question in the Technical section, or its marks differ from the `[n]` it carries |
| HR | a role has no question in the HR section |
| licence | a grade or document has no question in the Eligibility section |

- **Matching.** A question matches an item when it is worded as generation
  words it, or when it shares most of the item's words.
- **A reworded item.** A match on shared words only is a warning, so a
  reworded item is seen but does not fail.

## R3. Applies To, as warnings

On a gated sheet, the built-in audience rules (spec 14 A) say what a row
should be:
- shareholder rows, and rows about PLCs and share companies, are for those
  two types only;
- the company's own rows are not for individuals.

A row that is opened wider gets a warning. The team may have done that on
purpose.

## Where

- **The same check everywhere.** The checks run in `check`: the CLI, and
  the app's *Check an edited copy* and run check. The client's rules are read
  from `data/client_rules.csv`.
- **The rule names.** They are `RULE-REMOVE`, `RULE-REPLACE`, `RULE-REWORD`,
  `RULE-LIMIT`, `RULE-MARKS`, `CAT-TECHNICAL`, `CAT-HR`, `CAT-LICENCE` and
  `AT-AUDIENCE`. The report and the job's summary count them like the other
  rules.

## Acceptance

- PSI's final run 24, checked against PSI's rules, has 0 errors.
- The same workbook with these edits fails, each with its rule:
  - Ref 1's evidence set to 20 marks and Ref 2's to 10;
  - "PO" changed back to "LPO" on a general sheet;
  - an IBAN row added;
  - engineering equipment's warranty item set to 5 marks;
  - an HR role removed on 73.
- A shareholder row on 62 opened to Sole Proprietor gets an `AT-AUDIENCE`
  warning.
