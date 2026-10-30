# Spec 14: PSI's Version 2 comments

PSI's comments on the second batch (run 22). Three need the team's inputs
only (the categories table and client rules). This spec covers what the
code could not do. Agreed 2026-10-30.

## A. Who answers what, without a learned table

On a gated sheet, Applies To comes from the audience table, which is learned
from past sheets that fill it (spec 02 G7). After the bank was reset to the
Ethiopian Red Cross file, which has no Applies To, the table was empty. Every
row was asked of everyone except the Trade License and the Fayda ID.

When the table has no answer for a question, built-in rules decide:

| Question | Applies To |
|---|---|
| The Director / Shareholder section | Private Limited Company, Share Company |
| A question about private limited or share companies ("For private limited companies … audited accounts") | Private Limited Company, Share Company |
| Company name, office (main, branch, evidence), commercial / company registration, incorporation, memorandum, VAT, company profile | the firms: every accepted type except Individual Consultant |
| Everything else (contacts, bank, TIN, integrity, ESG, references…) | blank: everyone |

- Each answer is limited to the category's accepted types. A question none
  of them answers is removed, with a note, as for learned audiences.
- **A name for individuals.** A gated sheet that accepts individuals asks
  "Full name (as per Fayda ID)" (in Ethiopia; elsewhere "as per national ID
  or passport"), for Individual Consultant, right after the company name.
  This is skipped when the sheet already has a full-name question.
- **Order.** The learned table still wins. The rules only fill its gaps.

## B. A technical item can carry its marks

- **Marks.** A technical item in the categories table may end with its marks
  in brackets: `Attach Evidence Letter to extend warranty on goods sold for 1
  year minimum [3]`. Those are the row's marks.
  - When every item has marks, the Technical section's total is their sum.
  - When none has, the items share the section's total equally, as before.
  - When some have marks and some do not, the unmarked items share what is
    left of the section's total.
- **Rebalancing.** When the section's total changes, the difference is spread
  over the other sections, so the sheet stays at 100. The Technical section
  is left alone.
- **Wording.** An item already worded as a question or an instruction
  (starting "Attach", "Please", "Are you", "Do you", "Provide"…) is used as
  written. Other items are prefixed "Please attach", as before.

## C. A client rule that sets a sub-section's marks

A new client-rule action:

| action | does | example (PSI) |
|---|---|---|
| `marks` | the questions of the sub-section named in *match* carry *value* marks in total | Ref 1 → 15, Ref 2 → 15, Ref 3 → 10 |

- **Spreading.** The marks go to the sub-section's scored questions in
  proportion. With none scored, they go to its uploads.
- **Rebalancing.** The difference is spread over the sections no `marks` rule
  touched, and not over a Technical section whose items carry marks (B), so
  the sheet stays at 100.
- **When it runs.** Last among the marks rules in finalise, after Eligibility
  and the technical items, so no later rule moves these marks.
- **Unknown sub-section.** A sheet without the named sub-section is left
  alone.
- **Scope.** `groups` limits the rule as for the other actions.

## D. Wrapped questions show in full

Wrap text was already on, but rows had no height, so Excel showed one line.

- The writer sets each question row's height from its question: the text's
  length against the Criteria column's width and the font size. Long options
  or Applies To lists do not raise the row.
- A row is never lower than the template's.

## Acceptance

On synthetic data:
- **A.** On a gated sheet with an empty audience table:
  - the shareholder rows are for PLC and Share Company;
  - the company name is for the firms;
  - the phone number is for everyone;
  - "Full name (as per Fayda ID)" is for individuals, after the company name;
  - a category accepting only Sole Proprietor and Individual loses the
    shareholder rows.
- **B.**
  - Items marked [10], [3] and [2] give 10, 3 and 2, and the sheet totals
    100.
  - An item starting "Are you" keeps its wording.
- **C.**
  - Rules Ref 1 15, Ref 2 15 and Ref 3 10 on a sheet at 10/10/10 give
    15/15/10.
  - The other sections give up 10 in proportion, and the sheet totals 100.
- **D.** A 300-character question in a 58-wide column gets a row several
  lines high.

On PSI, the five Version 2 comments are audited on the rebuilt run.
