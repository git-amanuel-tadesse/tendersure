# Spec 18: A draft in several tabs, sub-category lines, and the client's previous list

Amref Health Africa in Ethiopia sent `Amref Categories Prequalification Nov 3
2026.xlsx`. The refinement flow cannot read it today: `read_draft` stops with
"no header row with a number column (No, #, Ref, Code) found" on every tab.

The file has:
- **A hidden, empty first tab:** `CoverP`.
- **Two draft tabs:**
  - Goods and services: 54 categories, with group rows `ICT`, `COMMUNICATION
    AND KNOWLEDGE MANAGEMENT` and `Engineering`.
  - Consultancy: 40 categories.
- **Two tabs of last cycle's results:** the 2025-2026 categories, each with the
  suppliers who qualified in it.

The team's answers (2026-11-04):
1. The two draft tabs are **merged into one draft**.
2. The 2025-2026 list is used for its **category names**: only the names, no
   codes, counts or suppliers.
3. **Draft categories only for now.** Criteria are generated once the client
   approves the list.
4. Consultancy #1's un-numbered lines become its **sub-categories**. The tool
   may also propose splitting them into 3–4 categories of related lines.

Every rule below is written for any client's file. Amref is the test case.

## A. Reading the draft

- **Number column headers.** `Serial`, `S.No`, `Sr. No` and `Serial No` are
  number-column headers, as `No`, `#` and `Ref` already are.
- **Hidden tabs.** Hidden tabs are never read and never offered. With no tab
  named, the reader takes the first visible tab that has a header row.
- **Group rows not in capitals.** An un-numbered row is a group row, even when
  it is not in capitals, if:
  - it has at most three words;
  - it does not start with a procurement verb (Supply, Provision, Hire,
    Repair, Construction, Design…);
  - the next row with text is numbered.

  Example: Amref's `Engineering`.
- **Old numbers inside a description.** A description that starts with a
  number and a space, on a row that already has its own number, loses that
  number. Example: Amref #37 "3 Provision of Strategic Communications…". The
  change is recorded as `Renamed`, with the old text.

## B. Several tabs, one draft

- **Choosing tabs.**
  - CLI: `--sheet` may be given more than once.
  - Web: the tab picker becomes a list of checkboxes, with hidden tabs left
    out.
  - Every chosen tab is read as above, and the rows are put into one draft in
    tab order.
- **The tab names the first group.** Rows before a tab's first group row take
  the tab's name as their group. A leading "Category for", "Catagory for" or
  "Category list for" is dropped, and the rest is put in Title Case. For
  Amref: `Goods and Services`, `Consultancy`.
- **Numbers.**
  - Each line keeps the client's number, shown with its tab.
  - IDs are unique across tabs, since both of Amref's tabs start at 1.
- **One list for every check.** Repeats, overlaps and merges (spec 01 R2–R4)
  are found across tabs. Example: event management is goods #15 and
  consultancy #13.

## C. Sub-category lines

- **The rule.** Un-numbered rows directly under a numbered category are its
  sub-categories when either:
  - they start with a dash or bullet (`–`, `-`, `•`); or
  - they are followed by such rows.

  A leading dash or bullet is dropped from the text. Example: Amref
  consultancy #1, "Project/Programme research evaluations", gets 5
  sub-categories:
  - Provision of Project-Based Consultancy Services
  - Feasibility Study and Assessment
  - Market Survey
  - Business Plan
  - Monitoring and Evaluation on Baseline, Midterm, End-term and etc.
- **Rows the reader can't place.** An un-numbered row that is neither a group
  row (A) nor a sub-category line still becomes a line of its own, as today.
  It carries a note: "No number in the draft: a category, or part of the one
  above?"
- **Proposing a split.** A category with several sub-categories may be
  proposed as `Separated`: a few new categories, each made of related
  sub-categories (the team suggests 3–4).
  - This is spec 01 R4. It is a proposal the team decides on, never applied
    on its own.
  - Only the model proposes it. Rules alone keep the category whole.

## D. The client's previous list

- **Input.** This is optional: a workbook with the client's previous
  categories, and a label for it (e.g. `2025-2026`).
  - CLI: `--previous FILE [--previous-label 2025-2026]`.
  - Web: a second upload on the Refinement page.
- **Two layouts are read:**
  - **A draft-layout list,** read as in A.
  - **A results list,** where each category is a header row above the
    suppliers who qualified in it:
    - **A header row** is a row whose text starts with a category code
      (`G/001/2025-2026 / …`), or a row merged across the table.
    - **The name** is the header's text, without the code or a closing count
      (`(17)`).
    - **Skipped:** "No category selected" headers, and repeated column-header
      rows ("Supplier Name, Score…").
    - **Every other row is a supplier row,** numbered or not. Example: Amref
      row 66, "Hey Trading One Member PLC", has no number.
- **Suppliers stay in the file.** Supplier rows are never stored, logged or
  sent to the model, and no supplier name appears in any output. The rows hold
  names, emails and phone numbers. Only category names leave the reader.
- **Using the names:**
  - **A draft line that matches a previous category** (case, spacing and
    small wording aside) shows "In the 2025-2026 list as '…'" in a new
    `Previous list` column of the revised workbook.
  - **A previous category with no matching draft line** is proposed as
    `Additional`, with the reason "In the client's 2025-2026 list, not in the
    draft", under a group `From the previous list`. As for every proposal,
    the team decides.
  - **The model gets the previous names** as "THE CLIENT'S PREVIOUS LIST",
    ahead of other clients' past exercises. It may mark a previous category
    as covered by a draft line, e.g. "computer accessories" by #30 End-User
    Computing. Such a category is shown as covered and not proposed. Without
    a model, every unmatched name is proposed, with a question for the
    client.

## Out of scope

- **Criteria for Amref.** They are generated after the client approves the
  list. Last cycle's consultant scoring (out of 50, pass mark 70%) is
  discussed then.
- **Requirements written into a description** ("Grade 5 and above", "in
  Oromia and Somali Region"). They stay in the text for now.
- **From the previous list:** the codes, the supplier counts and the scores.

## Acceptance

- **A.**
  - Amref's goods tab reads 54 categories.
  - `Engineering` is the group of #46–54.
  - #37–39 have no leading number and are `Renamed`.
  - With no tab named, the reader skips the hidden `CoverP`.
- **B.**
  - With both draft tabs chosen, the draft has 94 categories with unique IDs.
  - Goods #1–26 are in the group `Goods and Services`; consultancy lines are
    in `Consultancy`.
  - Consultancy #16 is `Repeated` of #7.
- **C.**
  - Consultancy #1 has the 5 sub-categories above.
  - No line is made from rows 5–9 of the consultancy tab.
- **D.**
  - The two results tabs give 98 names: 52 goods and 46 consultancy, with the
    repeated "Artistic Works…" counted once.
  - "Hey Trading One Member PLC", "BESYS Technologies PLC" and "No category
    Selected" are not names.
  - No supplier name is found in the revised workbook, the log or the model
    prompt (tested with "WEBZONE TRADING PLC").
  - "Hire of mobile toilets" and "Provision of taxi services" are proposed as
    `Additional`.
  - "Provision of psychometric, assessments…" matches consultancy #17, across
    tabs, and is not proposed.
- **On Amref:** the team runs refinement in the app and reviews the revised
  list (no criteria).

## As built (2026-11-04)

T114–T118 are built:
- **The reader** is in `refine.py` (`read_draft`, `_read_tab`). The
  previous-list reader is `previous.py`.
- **Tests:** `packages/core/tests/test_draft_tabs_previous.py`, and two API
  tests in `apps/api/tests/test_refine.py`.

Details the spec left open:
- **IDs across tabs** are `<tab position>-<number>`, e.g. `2-7`. A single tab
  keeps plain numbers.
- **Tabs of the previous list can be chosen too:** `--previous-sheet`, or the
  checkboxes on the web card. With none chosen, every tab laid out as results
  is read; with no results tab, the first list.
- **Matching** takes the same goods or services, whatever the verbs and small
  words (spec 09's `_same`), or text at least 92% alike. Looser text
  similarity matched "Provision of taxi services" with "Provision of cloud
  services".
- **API:**
  - `POST`/`DELETE /refine/previous`. The names are kept in
    `refine/previous.json`.
  - The draft's `sheet` became `sheets`. A `draft.json` written with `sheet` is
    still read.
- **On Amref (rules only):** 94 draft rows and 98 previous names, which give
  116 proposed categories. 23 of them come from the 2025-2026 list, and no
  supplier data appears in the output.
