# Spec 12: Client rules, country-first sources, and PSI's generation comments

PSI's comments on its first generated batch fall into three kinds:
- fixes the team makes in the app (refinement and categories table);
- client rules the app could not express;
- generation rules that were wrong for PSI and would be wrong for others.

This spec covers the last two. Agreed 2026-10-30.

## C. Client rules

A client rule changes every sheet of one client, or of some of its groups.
It is kept with the client and applied on every run, so the team says it
once.

Many of PSI's comments are about questions on most sheets, for example
conflict of interest on 72 rows and "LPO" on 111. These rows are copied from
past sheets without a flag, so they never become a decision card, and the
team had no way to act on them.

**C1. A rule** is one line: `client | action | match | value | groups | note`.

| action | does | example (PSI) |
|---|---|---|
| `remove` | removes every question whose wording contains *match* | match "associated with an employee" (conflict of interest); "sort code"; "IBAN" |
| `replace` | replaces the term *match* with *value*, as a whole word, case kept | LPO → PO; business permit → trade license |
| `reword` | replaces the whole wording of a question containing *match* with *value* | the office evidence question → "Attach evidence of the office: rent agreement and utility bill" |
| `limit` | keeps at most *value* questions in the section named in *match* | technical, 3 |

- **groups** limits a rule to some groups, matched on words of the group
  name.
  - A leading `!` excludes instead. `!engineering` means every group except
    those with "engineering" in their name.
  - Empty means all groups.
- **Matching** is case-insensitive and on words.
- **Notes.** A rule never touches the Declaration or Submission. Each change
  is noted on the row: "client rule: …".

**C2. Marks stay where they were.**
- A removed question's marks stay in its section, spread over the section's
  other scored questions, else over the sheet (the same as review removals).
- A `limit` keeps scored rows first, then the sheet's own, then added ones.
  The marks of the rows it drops stay in the section.

**C3. Order.** Client rules run first among the template rules, so the
Eligibility and naming rules see the final wording.

**C4. Where the team writes them.**
- **Web app:** a *Client rules* card on the Generation page, as a table with
  add, edit and delete. It applies on the next Build or Apply.
- **Storage:** the learned table `client_rules` (`data/client_rules.csv`),
  kept out of git, like the glossary.
- **CLI:** the same file is read by `generate`.

## S. Sources from the client's country first

PSI is an Ethiopian job, but most questions came from NRC's Kenyan sheets.

- **S1.** A sheet starts from the best match among the client's own sheets,
  then sheets **from the same country** (the country of the sheet code, else
  of its questions: `research.sheet_country`), then any.
  - Suggestions also prefer the same country.
  - The country is the exercise's (from its prefix).
- **S2.** A sheet built from another country's sheet says so in its notes,
  so the team sees it.
- **S3.** The template's structure (the 21 columns, sections and their order)
  is ours and does not depend on the source.

## R. Generation rules

- **R1. Sub-categories come from the draft only.**
  - A sub-category the model proposes is shown on the review line as a
    suggestion ("model suggests sub-categories: …"), never applied.
  - Applying a review keeps only sub-categories the line's draft rows state.
  - This reverses the 2026-10-29 choice to keep the model's sub-categories:
    PSI's own comment.
- **R2. The client's technical items are the whole Technical section.**
  - When the categories table lists technical items for a category, they
    replace the section's other questions.
  - The section keeps its marks, spread over the items.
  - An equipment list is one question per item: "Please attach evidence of
    ownership or rent agreement for a dump truck", and the same for a pick-up
    and so on.
- **R3. Organization type** offers the category's business types, on every
  sheet, gated or not.
  - The types are Sole Proprietor, **Partnership**, Private Limited Company
    and Share Company, plus Individual Consultant where the category is
    gated.
- **R4. A gated category's Eligibility** asks firms for the Trade License and
  individuals for a national ID: in Ethiopia the **Fayda ID**, elsewhere the
  national ID or passport. Applies To says which is which.

## Acceptance

On synthetic data:
- PSI's rules from C1 remove, replace, reword and limit as described: a
  group-limited rule only in its groups, and `!engineering` everywhere else.
  Sheets total 100 and the check passes.
- With Ethiopian and Kenyan sheets equally close, an Ethiopian client's
  sheet starts from the Ethiopian one.
- A line whose draft has no sub-categories applies none, whatever the model
  proposed.
- A category with technical items has exactly those as Technical questions,
  equipment one per item, and the section's marks kept.
- Organization type lists Partnership. A gated sheet's Eligibility has the
  Trade License for firms and the Fayda ID for individuals.

On PSI: the 16 comments, audited on the rebuilt final.
