# Spec 16: What PSI taught us

PSI's job took three rounds of client comments. Most comments were the
client's house rules, not bad questions: LPO or PO, trade license, no IBAN,
references at 15 / 15 / 10. The answers were often already in the client's
own files. Our checks lived in scratch scripts, and the review asked 151
questions while carrying too few answers between runs. This spec turns those
lessons into the product. Agreed 2026-10-30.

## A. Rules hold on every run

- **The check.** After the template rules, every sheet is checked against the
  client's rules and its category's items (spec 15) before it is written.
- **The cards.** A broken rule is a finding, as for the template rules. It
  becomes an `F-RULE-…` / `F-CAT-…` card in the review, so the run is not
  *Ready*.
- **The result.** It counts the rules checked and the rules broken. The
  *Ready to send* panel shows "Client rules: all N hold", or names what broke.

## B. A client profile before the first build

The client's house rules are asked once, as a short form, instead of being
learned from comments.

| Field | Becomes | Ethiopia default |
|---|---|---|
| Licence term | `replace` business permit → *term* | trade license |
| Purchase order term, and groups it does not apply to | `replace` LPO → *term*, groups `!…` | (none) |
| Local suppliers only | `remove` sort code, `remove` IBAN | yes |
| No conflict-of-interest question | `remove` associated with an employee, `remove` conflict of interest form | no |
| Office evidence wording | `reword` physically registered office / existence of office premises → *wording* | (none) |
| Reference marks (Ref 1, Ref 2, Ref 3) | `marks` Ref 1 / 2 / 3 | (none: ours) |
| Most technical questions | `limit` technical → *n* | (none) |

- **Storage.** The profile is stored as client rules, so there is one
  mechanism.
  - Rules of these shapes are read back into the form whether the team wrote
    them by hand or through the form.
  - Saving the form replaces them and keeps every other rule.
- **Before the first build.** The Generation page shows the profile card
  until it is saved once. It is prefilled with the country's defaults.

## C. Rules proposed from the client's own files

Suggestions only; the team adds each with one click.

- **From the benchmark workbook** (spec 08):
  - **Reference marks:** the marks most of the client's tabs give each
    reference sub-section.
  - **Terms the client uses:** from a known list of pairs (LPO/PO, business
    permit/trade license, KRA PIN/TIN, VAT certificate/VAT registration
    certificate), where the tabs use one and never the other.
  - **The technical limit:** the most technical questions any tab asks.
- **From an edited copy** (spec 11 F2, spec 15). A change repeated on two or
  more sheets:
  - the same question removed → `remove`;
  - the same term replaced → `replace`;
  - the same sub-section's marks set → `marks`.
- **Skipped.** A suggestion an existing rule already covers is not shown.
- **Where.** Benchmark suggestions are under the Client rules card. Edit
  suggestions are under the result of *Check an edited copy*.

## D. A calmer review

- **Stable marks cards.** A marks card's ID names its source sheet and the
  categories it covers, not their current marks. An answer is carried to the
  next run when the marks change, for example after a client rule moved them.
- **A carried answer a client rule overrides.** An answer whose split gives a
  section other marks than the client's `marks` rules set is applied for the
  other sections. Its status says which section the rule kept, and why.
- **Accept every proposal.** This was already there: "Accept as proposed"
  answers every card shown with its proposal. Nothing to build.

## E. Proper inputs

- **Technical items.** In the categories table they are edited as a list:
  one line per item, with its marks in a box beside it. They are stored as
  `text [n]` (spec 14 B).
- **Reference marks.** These are a profile field (B), not a rule the team has
  to know how to write.

## F. See what the client will see

- **Preview.** A run's sheets can be previewed in the browser: sections,
  sub-sections, the wrapped questions, marks and Applies To, laid out as in
  Excel.
- **Where.** A sheet picker on the Generation page.

## G. Protect learned knowledge

- **The warning.** When a gated category is built while the audience table
  is empty, the run says so: "Applies To from built-in rules only: no past
  sheet in the bank fills Applies To".
- **Where it shows.** The line appears in the run log and the QA report's
  notes.

## Acceptance

- **A.**
  - A run whose rules all hold says so.
  - A rule broken in a plan (synthetic) is an `F-RULE-MARKS` card, and the run
    is not *Ready*.
- **B.**
  - PSI's rules read back as a filled profile.
  - Saving a profile with Ref marks 15 / 15 / 10 writes three `marks` rules
    and keeps the engineering rewords.
  - A new Ethiopian exercise shows trade license and local suppliers
    prefilled.
- **C.**
  - On PSI's draft, the benchmark proposes Ref marks if its tabs share them,
    and PO if its tabs never say LPO.
  - An edited copy with IBAN removed on three sheets proposes `remove IBAN`.
- **D.**
  - Two runs whose marks differ give the same marks-card ID.
  - A carried "trade_reference 30" under 15 / 15 / 10 rules is applied for
    the other sections, and its status names the rule.
- **E.** A technical item saved with marks 3 reads back as `… [3]`.
- **F.** The preview of a run's sheet lists its rows in order, with marks.
- **G.** A gated build with an empty audience table carries the warning.
