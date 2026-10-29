# Spec 07: The client's brief, and requirement columns in the draft

Clients say what they want in two ways:

- **In words.** "For engineering works we want G5 contractors with their own
  dump truck."
- **In extra columns of their category draft.** PSI's draft adds Contractor
  Level, HR and Technical Specification on its engineering categories, and
  marks one category "Includes Individual Consultant".

Today the tool reads neither. The draft reader keeps only number,
description and group. It drops the sub-category rows under a category and
every extra column. The `requirements` field reaches only the reviewer's
prompt, and nothing fills it.

Agreed 2026-10-29:

- one brief per run, with no per-category note box;
- the brief ranks above the bank but below the template rules;
- the structured columns are applied by rules, so they work without a model;
- the free-text brief needs a model.

## B. The brief

**B1. Input.**
- **Web app:** a text box in the run settings of Refinement (S3) and
  Generation (S5). It is stored with the exercise and filled in again on the
  next run.
- **CLI:** `--brief <text | file.txt>` on `refine`, `generate` and `check`.
- Each run records the brief it used, in `runs/<n>/settings`.

**B2. Who reads it.** Only the model steps that shape this client's output.
The brief goes first in the prompt, before anything from the bank:
- the refine proposal;
- the generation proposer (`selectors`);
- the reviewer;
- the AI check.

It is not given to naming, Yes/No or research. Those are learned facts
shared by every client (specs 04, 05), and a brief would make them
client-specific.

**B3. Priority.** The brief ranks above history and other clients' practice:
which questions to add, which to remove, and the wording. The template rules
(spec 02 G1–G11) still run afterwards, followed by the pre-write check. A
brief that asks for something a rule forbids ("total 120 marks", "make VAT
mandatory everywhere") loses to the rule. The QA report says so: "brief
asked …; rule G4 kept …".

**B4. Visible.** Each addition, removal or rewording the model makes because
of the brief carries `from_brief`. Its Decisions line is labelled "from the
client's brief". It is still a proposal that the team answers.

**B5. Without a model.** The Summary tab of the QA report says "client's
brief not applied: no model".

**B6. Private and cached.** The brief is anonymised and logged like every
prompt. It is part of the prompt, so changing it gives a new cache key.

## R. Requirement columns in the draft

**R1. Read.** `read_draft` finds these optional columns by their header
label:

| Field | Headers it accepts |
|---|---|
| sub-categories | Sub-category, Sub category |
| licence | Contractor Level, Licence, License, Grade, Registration |
| hr | HR, Human Resource, Key Staff, Personnel |
| technical | Technical Specification, Technical, Equipment |

- A row with no number and no description belongs to the category above, so
  the sub-category values on PSI's continuation rows are kept.
- A cell anywhere on the row that says "Individual Consultant" (PSI writes it
  in a column with no header) adds Individual Consultant to Applies To and
  turns the gate on (the T19 rule).
- A sub-category cell that names a document (certificate, certification,
  licence, permit, registration) is a licence requirement, not a
  sub-category. Refine lists it as a question for the client.

**R2. Items.** A cell is split on commas into items.
- A slash keeps alternatives together, so "Site engineer/Residential
  Engineer" is one role.
- "minimum N years" stays with the item it follows.
- A list in brackets, "(Mixer, Pick up, Dump Truck)", is one equipment item.

The split is shown for correction (R3) before anything is built.

**R3. Carried through refinement.**
- Refine keeps the three fields on its lines. The Revised tab gains the
  columns *Licence*, *HR* and *Technical*, editable like Eligibility.
- Merging two drafts joins their items.
- The final list writes them to the categories table as `licence`, `hr` and
  `technical`, with items separated by `|`.
- The API and the Categories page (S4) show and edit them.

**R4. Applied in generation.** Rules in `finalise`, before the template
rules, so they work with or without a model:

- **Licence.** Each item adds two rows to the Eligibility section:
  - an upload, mandatory and marked Eligibility, with 0 marks: "Please attach
    a valid certificate of competence/grade certificate from the relevant
    authority (<item>)";
  - a typed row before it, "Please specify your grade/level", which is not
    marked Eligibility.
- **HR.** One row per role in the Human Resource section: "Attach evidence
  (CV, contract and education/experience) for <role>[ with at least N years
  of experience]".
  - The skeleton's own role rows (CV/evidence for a named role) are replaced
    in order. Each listed role takes the marks of the row it replaces, so
    PSI's benchmark (5 per role) holds.
  - Roles beyond the skeleton's are added with 0 marks.
  - Skeleton roles left over are removed, each listed in the QA report.
- **Technical.** One upload row per item in Technical Specifications:
  "Please attach <item>". An equipment item becomes "Please attach evidence
  of ownership or rent agreement for: <list>".
  - Each item replaces a matching skeleton row and keeps its marks.
  - An item with no matching row is added with 0 marks.
  - The section is created if the skeleton has none.
- **Notes.** Each row carries a note, "from the category's HR column" and so
  on. Marks are not rebalanced: a 0-mark row leaves the sheet at 100, and the
  QA report's M items let the team move marks.
- **Names.** Short Name and Acronym follow G8 as for any row.
- **Model.** With a model, the three fields also fill `requirements`, so the
  proposer and reviewer word the rest of the sheet to fit.

## Acceptance

On synthetic data (`tendersure.testing`):

- A draft with a SUB-CATEGORY column and continuation rows gives each
  category its sub-categories.
- Contractor Level, HR and Technical cells become items as in R2.
- An "Includes Individual Consultant" cell sets the gate and Applies To.
- These survive refine, apply and the categories CSV unchanged.
- A works category with HR "Project Manager, Site engineer, Foreman", built
  on a skeleton with two CV rows of 5 marks:
  - its first two roles have 5 marks each;
  - Foreman has 0;
  - the sheet totals 100;
  - the check passes.
- With `--brief`, the logged prompts of the four B2 steps start with the
  brief. Naming, Yes/No and research prompts do not contain it.
- Without a model, the QA Summary says the brief was not applied.

On PSI's draft, run by the team: the engineering categories 65–77 carry
their licence, HR and technical rows, and category 62 is gated with
Individual Consultant.
