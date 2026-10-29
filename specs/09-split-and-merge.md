# Spec 09: Split and merge in the review

When refining, the team splits categories that couple what different
suppliers provide, merges ones that are very close, and applies the client's
feedback. The team says deciding these is where they struggle most.

Today the review answers a split or a merge only as a whole. There are four
decisions per line: accept, reject, remove, or edit (new wording). That
leaves three gaps:
- a split proposed by the model cannot be corrected piece by piece;
- a line the proposal kept cannot be split or merged by the team or the
  client;
- an "Overlaps with #13: merge them, or keep both?" question cannot be
  answered with the merge itself.

Without a model (as when the account ran out of credit on PSI), the rules
propose no split or merge at all.

Agreed 2026-10-29:
- split and merge can be edited both in the browser and in the workbook sent
  to the client;
- without a model, the rules propose candidates as suggestions;
- nothing is applied until someone chooses it.

## S1. Two more decisions

**Split** means each piece becomes a category of its own: a line in the final
list and its own sheet. **Merge** means the lines become one category: one
line and one sheet. Neither turns a category into sub-categories, or
sub-categories into categories, except as stated below.

- **split.** The line becomes the pieces listed in *Split into*, one
  description each, in Title Case. Every piece keeps the line's draft rows,
  eligibility, Applies To and licence, HR and technical items.
  - When the line is split by its sub-categories, each one becomes a category
    of its own, and so carries no sub-category question. ("Supply of Relief
    Non-Food Items" split by its five items gives five categories.)
  - Any other piece keeps the sub-categories that did not become categories.
  - A split into fewer than two pieces is a problem, and the line is kept.
- **merge.** The lines listed in *Merge with #* (the revised list's #
  numbers) fold into this line.
  - The line keeps its own description, or the wording in Client comment
    when one is typed.
  - It takes the draft rows, sub-categories, eligibility and requirement
    items of all the lines, and Applies To from all of them.
  - Each folded line is listed as merged into this one.
  - A # that does not exist, is this line, or has already been folded
    elsewhere is a problem, and nothing is merged.

After either decision, every draft row is still on a line (spec 01 R10). A
line folded by a merge is not counted as removed.

## S2. Suggestions without a model

Rules fill two read-only columns that a single action can copy into the
decision:

- **Suggested merge:** the # numbers of the lines this one overlaps (the
  existing overlap test). The question for the client is kept.
- **Suggested split:**
  - a line whose draft lists two or more sub-categories gets those
    sub-categories, each as "<verb> of <sub-category>" ("Supply of
    Batteries");
  - a line that joins two procurement heads ("Supply of X and Provision of
    Y") is split at the join.

  A suggestion is never applied on its own.

With a model, its Separated and Merged lines are answered as before. A
Separated line can be corrected with *edit* per piece, or split further with
*split*.

## S3. In the workbook

The Revised tab gains three columns:
- *Split into*, with pieces separated by `|`;
- *Merge with #*, with numbers separated by commas;
- *Suggested split / merge*, read only.

The Decision column takes `split` and `merge`. The Summary tab explains both.
`read_revised` and the import read the new columns back.

## S4. In the browser

- The Review table shows each line's # number.
- The Decision select adds *split* and *merge*:
  - *split* opens an editable list of pieces, with add and remove, filled
    from the suggestion when there is one;
  - *merge* takes the # numbers, with the overlap suggestion as a button.
- The API's line change takes `split_into` and `merge_with`.

## Acceptance

On synthetic data:
- A kept line split into three edited pieces gives three final categories
  with the line's draft row and items. A piece named after a sub-category
  has none.
- Two overlapping lines merged give one final category with both draft
  rows. Its question is kept, and nothing is reported as removed.
- A bad split (one piece) or a bad merge (an unknown #, or itself) is listed
  as a problem and changes nothing.
- A draft with sub-categories, and one with "Supply of X and Provision of
  Y", get suggested splits; an overlapping pair gets suggested merges. None
  is applied unless chosen.
- The workbook round trip keeps Split into and Merge with #.
- The browser review gives the same final list as the workbook.
