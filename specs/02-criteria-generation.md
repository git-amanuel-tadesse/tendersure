# Spec 02: Criteria generation

Feedback items 1, 2, 3, 4, 5, 7, 8, 10 and 11. Changes to the existing
`generate` flow; everything not mentioned here (retrieval, AI proposals and
review, the Decisions tab, marks splits) stays as it is.

## Inputs

- **Categories table** (`data/<client>_categories.csv`), from the advert or
  from spec 01. Columns: `number, group, description, sub_categories, kind,
  client, version, requirements, eligibility, gate_question, applies_to`.
  - `sub_categories`: from the advert, separated by ` | `.
  - `eligibility`: extra eligibility documents for this category, ` | `.
  - `gate_question`: `TRUE` when the category accepts Individual Consultants.
  - `applies_to`: the business types accepted, comma-separated, e.g.
    `Sole Proprietor, Private Limited Company, Share Company, Individual Consultant`.
- **The bank**: the client's past sheets (question content and styling).
- **Learned files** under `data/`, editable by the team (see G8, G7, G10):
  `glossary.csv`, `audience.csv`, `yesno_polarity.csv`.

## G1. Template (item 7)

- Every sheet is written in the 21-column layout, columns B to V:

  | Col | Header | Col | Header |
  |---|---|---|---|
  | B | Section | M | Mandatory |
  | C | Sub Section | N | Attachment |
  | D | Criteria | O | Compliance (upload flag) |
  | E | Marks | P | Options |
  | F | Eligibility | Q | Options Scores |
  | G | System | R | Eval Options |
  | H | Compliance (marks) | S | Eval Options Scores |
  | I | Evaluation | T | Verify Against |
  | J | Archive Short Name | U | Gate Question |
  | K | Acronym | V | Applies To |
  | L | Answer Type | | |

- Row 2: `CODE: Description`. Row 3: `Marks` banner merged over E–I.
  Row 4: headers. Sub Total and Total Marks rows sum E, G and H.
- Evaluation, Eval Options, Eval Options Scores and Verify Against are
  written as headers and left empty (Kenya template only).
- Styling still comes from the skeleton sheet, matched by **field**, not
  column position. Columns the skeleton lacks borrow a sibling's style
  (Eligibility and Gate Question from Mandatory; Evaluation from System;
  Eval Options, Verify Against and Applies To from Options).
- **Reading** (ingest and check) finds each field by its header label, so
  14-, 16- and 21-column sheets all read correctly. "Compliance" appears
  twice: the first is the marks column, the second the upload flag.

## G2. Section order

Declaration · Company Information · **Eligibility** · Registration and
Statutory Requirements · Director/Shareholder Information · Business
Integrity, Risk and Litigation · Sustainable Procurement (ESG) · Technical
Specifications · Service Level Requirements · Human Resource · Business
Experience · Trade Reference · Financial Performance · Submission.

Sections the skeleton does not have stay absent, except Eligibility, which is
always present.

## G3. Eligibility (items 3 and 7)

- The Eligibility section holds:
  1. the Trade License upload, moved from Registration and Statutory
     (ERCS wording: "Please attach a valid Trade license"); inserted from the
     glossary if the skeleton has none;
  2. one upload row per document in the category's `eligibility` column.
- Commercial registration certificate, TIN certificate and number, tax
  clearance and VAT stay in Registration and Statutory. A VAT question is
  kept when the client's sheet has one; it is never added (ERCS sheets have
  none).
- Every row in the Eligibility section: Eligibility = TRUE, answer type 5,
  Mandatory = TRUE, Compliance flag = TRUE, marks 0. No row outside the
  section has Eligibility = TRUE.

## G4. Marks in two columns (item 3)

- Every question row repeats its Marks value, 0 included, in System or
  Compliance. This changes what the clients' own sheets did (624 of 1,105
  ERCS rows leave both blank).
- Which column, when the source row has neither: Compliance if the row is an
  upload (type 5), or its Attachment, Compliance flag or Eligibility is TRUE;
  System otherwise.
- A source row that already carries the value in System or Compliance keeps
  its choice.
- A blank Marks becomes 0.
- Marks = System + Compliance on every row; section subtotals and the total
  follow; the total is 100 (unchanged rule: a sheet that is not 100 is not
  written).

## G5. Sub-category question (item 1)

- Sub-categories come from the advert. Extraction must capture all of them:
  today only 5 of 58 ERCS categories have sub-categories and the team reports
  some were missed. The ERCS notice is re-checked against the table as part
  of this spec.
- When a category has sub-categories, the sheet has one question, placed in
  Company Information / Information right after the business-type question
  (precedent: ERCS-ET-2026-039):
  - Criteria: "Please select the subcategory you would like to participate in"
  - Answer type 3, Mandatory TRUE, Marks 0, System 0
  - Options: the sub-categories joined by `, `. A comma inside one
    sub-category ("Sugar, Milk Powder") would split it into two options, so
    it becomes ` / ` ("Sugar / Milk Powder"), noted on the row.
  - Options Scores: `0` per option.
- If the skeleton already has this question, its options are replaced with
  this category's. If the category has no sub-categories, a copied
  sub-category question is removed (it would carry another category's
  options).

## G6. Merged blocks and borders (item 2)

- Consecutive rows of one Section form one merged cell in column B; within
  it, consecutive rows of one Sub Section form one merged cell in column C.
- A row inserted by the AI or from suggestions takes the Sub Section of the
  row above it in the same section (or the row below, if it is first). Today
  these rows arrive blank and split the block: 27 cases in 20 ERCS sheets.
- Every cell B–V of question, Sub Total and Total rows has a border, and each
  merged block has its outer border on all four sides.
- A Sub Section the client's sheet leaves blank stays blank (decided
  2026-10-28; e.g. ERCS-041's one-row Technical section).

## G7. Gate Question and Applies To (items 4 and 8)

- The gate is the question that asks the supplier's business type (the team
  calls it "Type of business"; ERCS sheets label it "Organization type"):
  Sole Proprietor / Private Limited Company / Share Company / Individual
  Consultant. ERCS's other "Type of Business" question (Manufacturer /
  Trader…) is not the gate.
- **Category `gate_question` = TRUE:**
  - The business-type question's options become the category's
    `applies_to` list, exact text, scores all 0, and its Gate Question = TRUE.
    It is the only row with Gate Question = TRUE.
  - Every other question gets Applies To = the business types that should
    answer it, written with the gate's exact option text. Examples: company
    name → Sole Proprietor, Private Limited Company, Share Company; full name
    as per ID and ID/passport → Individual Consultant; VAT and shareholder
    details → Private Limited Company, Share Company; trade references,
    declaration → blank (everyone).
  - Who answers what comes from `data/audience.csv` (question → business
    types), seeded from the SNV sheets that already do this. The value
    written is that audience intersected with the category's `applies_to`.
    A question with no known audience is written blank and listed in the QA
    report for the team.
  - Individual-only questions (full name per ID, ID/passport) are added from
    the bank if the skeleton lacks them.
- **Category `gate_question` not TRUE:** Gate Question and Applies To are
  empty on every row; Individual Consultant is not offered; individual-only
  questions are removed.
- A categories table where `gate_question` and the presence of Individual
  Consultant in `applies_to` disagree is an error for that category; the
  sheet is not written.

## G8. Archive Short Name and Acronym (item 5)

- Required on every row that is an upload (type 5) or has Attachment,
  Compliance flag or Eligibility = TRUE. Other rows are left as they are.
- Source: `data/glossary.csv` (question → short name, acronym, status),
  seeded from the bank: the client's most common pair for the question, then
  other clients'. Example pairs: TIN Certificate / TIN, Tax Compliance
  Certificate / TCC, Reference 1 / REF 1.
- A required row with no glossary entry gets a drafted Short Name (at most
  four words; by the model, or by rules without one), saved to the glossary
  as `drafted`. Once the team confirms it, it is reused as is.
- A new Acronym is never invented: it is built from the Short Name by the
  team's letter rule (decided 2026-10-28): the initials of its words, small
  words skipped (Tax Compliance Certificate → TCC); a word already in
  capitals or a number stays whole (Structural Engineer CV → SECV); after
  such an abbreviation a closing Certificate/License/Policy/Form is dropped
  (TIN Certificate → TIN, SHE Policy → SHE); one word gives its first three
  letters (Accounts → ACC). Acronyms the clients already use (COI, COIF,
  DAF/MAF, TIN/PIN) are kept.
- Within a sheet, acronyms are unique; repeated blocks are numbered
  (REF 1, REF 2, REF 3; Staff 1, Staff 2).
- A copied pair that contradicts the glossary is reported, not changed
  (e.g. ERCS "Website address" carries the short name "Main Office
  Location").

## G9. Option scores (item 10)

- For answer types 2 and 3, the row's Marks must equal one of its option
  scores, and no option may score more than the Marks.
- The rule applies to Marks whichever column (System or Compliance) carries
  them.
- A copied row that breaks the rule is always fixed, so it never blocks the
  sheet:
  - a Yes/No row: by G10;
  - any other row: scores scaled in proportion so the best option equals the
    Marks, rounded (NRC "Lead time": 0 marks with scores 1,2,3 becomes
    0,0,0).

  Each fix is noted on the row and listed in the QA report as a decision, so
  the team can change the marks or the scores instead.

## G10. Yes/No questions (item 11)

- A Yes/No question is a type 4 row, or a type 2 row whose options are Yes
  and No.
- Its **polarity** (is "Yes" the good answer?) is decided once per question
  and kept in `data/yesno_polarity.csv`. Unknown polarity: the model
  proposes, the team confirms in the QA report; until confirmed, the row is
  left as copied and flagged.
- Positive, or Marks 0 → answer type 4, no options or scores.
- Negative with Marks > 0 → answer type 2, Options `Yes,No`, Scores
  `0,<marks>`. This includes compliance-marked rows such as PEP and
  litigation.
- This rule overrides the client's past sheets and the convention
  correction in `trust.py`.

## G11. Output check

Every generated sheet is run through the spec 03 rules before it is written.
A generated sheet with any error is listed as NOT WRITTEN with its errors,
as a sheet not totalling 100 is today.

## Acceptance

- The ERCS 2026 batch regenerated in the 21-column layout, opening cleanly
  in Excel, with:
  - an Eligibility section holding the Trade License (+ category documents),
    Eligibility TRUE on exactly those rows;
  - no row with Marks but blank System and Compliance;
  - a sub-category question on every category with sub-categories, and on
    no other;
  - no split Section/Sub Section blocks;
  - no required row without Short Name and Acronym;
  - no Yes/No or option-score rule violation;
  - Gate/Applies To filled only where the categories table says so.
- The NRC sheets ingest with the right fields (Eligibility read as
  Eligibility, not System).
- Existing tests pass; each rule G3–G10 has a test on synthetic data.
