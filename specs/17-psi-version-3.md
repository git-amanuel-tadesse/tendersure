# Spec 17: PSI's Version 3 comments

PSI's third round of comments. The team's answers on the unclear points came
on 2026-11-02:
- the printing questions go on all four printing categories;
- the assessment paragraphs become one row each;
- the new list keeps each category's questions;
- the new question's marks come from the other sections.

| # | PSI's comment | How |
|---|---|---|
| 1 | Add "Please confirm if you extend the credit period for 30 Days" to Financial Performance, everywhere (2 marks) | client rule `add` (A) |
| 2 | Sub Total should be a sum formula | formulas on by default (C) |
| 3 | Use the new category list, keep the questions as is | import the client's list (D); run 24 added to the bank as PSI's own sheets |
| 4 | Registration and Statutory: "Please attach VAT Certificate" for every category except Medical | client rule `add`, groups `!medical` (A) |
| 5 | Trade License short name and acronym are Trade License / TL, not Business Permit / BP | B |
| 6 | Printing and audio-visual equipment: two machinery questions in Technical | client rule `add` on categories 12 and 28–31 (A) |
| 7 | Agriculture, and Assistive Devices: PSI's assessment paragraph | client rule `add`, one row each: Agriculture group, and category 22 (A) |

## A. A client rule that adds a question

| action | does | example (PSI) |
|---|---|---|
| `add` | adds the question *value* to the section named in *match*, at its end, unless the sheet already asks it | financial → "Please confirm if you extend the credit period for 30 Days [2]" |

- **Marks.** *value* may end in `[n]`, the row's marks, as for technical items
  (spec 14 B). The other sections give up the difference in proportion, and
  the section with the added row keeps it. A row without `[n]` has 0 marks.
- **The answer type comes from the wording:**
  - "attach…" is an upload (5);
  - a yes/no question ("Please confirm…", "Do you…", "Are you…", "…Yes/No")
    is Yes/No (4), with its marks in System;
  - "provide details…", "describe…" and other wording is text (1).
- **Scope by category.** `groups` may name categories by number, so `#12,
  #28` limits a rule to those categories (for every action). Words still name
  groups, and `!` still excludes.
- **Order.** Added rows are added after the other client rules. A `limit`
  never removes them and does not count them: PSI's limit of three technical
  questions applies "unless otherwise mentioned".
- **Answers carry over.** A marks answer carried to the next run already
  counts the added row's marks. The added marks are taken out of that answer
  before it is applied, and the row brings them back, so its section does not
  grow on every run.
- **The check (spec 15).** A sheet in the rule's scope without the question
  is an error, `RULE-ADD`.

## B. Trade License is named Trade License

- **The name.** The Trade License upload is always short name *Trade
  License*, acronym *TL* (template rule, spec 02 G2), whatever the glossary
  learned. The Red Cross file named it Business Permit / BP.
- **Short names follow replace rules.** A `replace` rule also changes the
  short name. An acronym made of the old name's initials is made again from
  the new name.

## C. Sum formulas by default

- **The default.** Sub Total and Total rows are `=SUM()` formulas: PSI asks
  for it, and the source sheets use them. Literal numbers stay an option
  (CLI `--values`, the Build card's tick box).
- **Checked.** Our check reads formula sub-totals correctly.

## D. Import the client's approved category list

- **Input.** The categories table can be imported from the client's list as
  an `.xlsx` in the draft's layout (number, description, sub-categories,
  groups as header rows), not only from a CSV.
- **What is taken as written.** Descriptions are taken as written, with
  spacing tidied, as PSI asked: "the wordings should be as is".
- **What is carried from the old table.** A category whose description
  matches one in the old table (case and spacing aside) keeps the team's
  settings for it: gate, Applies To, eligibility, licence, HR, technical,
  requirements. Sub-categories come from the client's file.
- **The old table.** It is kept in `history/`, as for every import.

## Acceptance

- **A.**
  - On synthetic data, an `add` rule puts its question at the end of the
    section, with its marks, and the sheet totals 100.
  - A second build with the marks answer carried keeps the section's total.
  - `#2` scopes a rule to category 2.
  - A `limit` keeps added rows.
  - A missing added question is `RULE-ADD`.
- **B.** The Trade License row is Trade License / TL even when the glossary
  says Business Permit / BP.
- **C.** A default build writes `=SUM(` in its Sub Total rows, and the check
  passes.
- **D.**
  - PSI's final list imports as 77 categories.
  - 62 and 63 keep their gate, and 68–77 keep their technical, HR and licence
    items.
  - Category 10 is Fumigation.
- **On PSI:** all three rounds of comments, audited on the rebuilt run.
