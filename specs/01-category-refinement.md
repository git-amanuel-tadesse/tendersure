# Spec 01: Category refinement

Feedback item 9. A flow of its own, separate from criteria generation.

## Purpose

Turn a draft category list, agreed between the team and the client, into a
clean revised list the client can review and approve, then apply the client's
feedback to produce the final list and the categories table that criteria
generation reads.

The number of categories is a business decision: fewer categories means less
revenue, more means more. The target is a middle ground, usually 50–60, but
it depends on the requirements (anything from about 23 to 120). The tool
neither minimises nor maximises the count; every merge and split carries a
reason.

## The team's process (from the NRC exercise)

1. Team and client draft the list together (NRC: `NRC Categories`).
2. The team revises it, labelling every change and noting questions for the
   client (NRC: `Revised and Add. Categories`). **The tool does this step.**
3. The client reviews and gives feedback.
4. The team applies the feedback (NRC: `Final Categories`). **The tool
   applies it.**
5. The advert is produced, with a G/S/W code per category (NRC: `Advert`).
   Codes are assigned by the team, not the tool.

## Inputs

- A draft list as Excel or CSV. Excel drafts are read as the clients write
  them: a number column, a description column, and group header rows (text,
  no number) that may sit in either of the first two columns and may be
  merged across them (both occur in the Red Cross draft).
- `--client`, and optionally `--target 50-60` (reported against, never
  enforced).
- The bank's past category lists (ERCS, NRC, SNV, ILRI), as evidence for
  overlaps and for commonly requested categories.

## Step A: propose a revised list

Command: `tendersure refine <draft> --client X [--target a-b] --out revised.xlsx`

### Rules

- **R1. Clean the wording.** Fix spelling ("eqiupment", "pharmaeceutical",
  "accomodation", "Trapulin") and use Title Case, as NRC's final list does.
  Keep the "Supply of" / "Provision of" opening. A wording change keeps the
  category and is recorded as `Renamed` with the old text.
- **R2. Repeated.** A category that appears twice (Red Cross #52 and #59) is
  marked `Repeated` and points to the one it duplicates.
- **R3. Overlaps.** Categories that cover the same goods or services
  (Red Cross #11 / #19 ICT equipment; #8 / #9 / #20 waste and disposal) are
  either proposed for `Merged`, with a reason, or kept apart with a question
  for the client. Never merged silently.
- **R4. Separated.** A category that bundles distinct trades or procurement
  kinds (NRC: "Cleaning, Gardening, Sanitary, Garbage Collection and pest
  control…"; works split into six) is proposed for `Separated`, listing the
  new categories.
- **R5. Removed.** The tool does not remove categories on its own, except a
  `Repeated` one folded into its twin. Scope removals are the client's call
  (NRC removed relief food, pharma, fuel… at review); the tool may raise a
  question instead.
- **R6. Additional.** Categories common in comparable past exercises but
  missing from the draft may be suggested as `Additional (suggested)`, with
  the exercises they come from. The team accepts or drops each.
- **R7. Questions for the client.** Where the draft is ambiguous, the tool
  writes a question, as the team did for NRC ("Any specific languages?",
  "Broker or underwriter?", "What type of gardening?").
- **R8. Proposed columns.** For each category the tool proposes:
  - **Sub-categories**, when the description lists distinct items the
    client may want suppliers to choose between.
  - **Eligibility**: only documents the draft itself states, e.g. "IATA
    Registration Certificate" for "(IATA Registered Only)", a CALP
    certificate for "(CALP Certified Trainers Only)". Each becomes a
    mandatory pass/fail upload in the category's sheet, so other licences
    the model proposes go to a separate "Suggested eligibility (not
    applied)" column; the team copies any the client wants (decided
    2026-10-29).
  - **Gate Question** and **Applies To**: whether Individual Consultants may
    apply (typically consultancy, training, translation), and the accepted
    business types. See spec 02, G7.
- **R9. Groups.** The client's own groups are kept. Regrouping is a question
  for the client, not a change.
- **R10. Traceability (enforced in code).** Every draft row appears in the
  output with a status; every proposed category lists the draft numbers it
  comes from. A draft row with no status, or a proposed category with no
  source and no `Additional (suggested)` label, is an error and the file is
  not written.

### Output: `revised.xlsx`

- **Draft** tab: the list as received.
- **Revised** tab, one row per proposed category, in the NRC layout extended
  with the agreed columns:
  `# · Category Code · Category Description · Status · From draft # · Reason ·
  Question for client · Sub-categories · Eligibility · Gate Question ·
  Applies To · Decision · Client comment`
  - Status: `Kept`, `Renamed`, `Merged`, `Separated`, `Repeated`,
    `Additional (suggested)`, or `Removed` (only at Step B).
  - Category Code: left blank for the team.
  - Decision / Client comment: empty, for the review.
- **Summary** tab: draft count, proposed count, the target range if given,
  and counts by group and by status.

## Step B: apply the review

Command: `tendersure refine --apply revised.xlsx --out final.xlsx --categories-out data/<client>_categories.csv`

- Each Revised row's Decision is `accept` (default when blank), `reject`
  (revert to the draft form), `remove`, or `edit` with the new wording in
  Client comment.
- A free-text client comment that is not a decision is turned into a
  proposed change by the model and listed for the team to confirm; it is not
  applied on its own.
- Output:
  - **Final Categories** tab, renumbered in order, in the NRC layout.
  - The categories table (`number, group, description, sub_categories, kind,
    client, version, requirements, eligibility, gate_question, applies_to`),
    the input of spec 02. Codes the team typed in are carried through.
- The same traceability check as R10 runs again.

## Acceptance

- On the Red Cross draft: #52/#59 flagged `Repeated`; the typos above fixed;
  #11/#19 and #8/#9/#20 raised as merges or questions; every one of the 74
  draft rows accounted for.
- Evaluation against NRC history: run Step A on `NRC Categories` and compare
  with `Revised and Add. Categories` and `Final Categories`. Report how many
  of the team's separations, merges and additions the tool proposed, and how
  many proposals the team did not make.
- No model call leaves the machine without the privacy layer.

## Out of scope

- Assigning G/S/W advert codes.
- Writing the advert itself.
