# Spec 08: The client's benchmark criteria

Some clients send sample criteria with the categories: one tab per kind of
category ("PSI General", "PSI ICT", "PSI Eng con"…), saying "use these for
mark allocation". These tabs are not history:
- they have no sheet codes and no client on them;
- one may carry another client's code (PSI's Consultancy tab reads
  SNV-ET-2026_019).

The bank sets such tabs aside as unusable (ID-NO-CLIENT), so today the
client's own benchmark has no effect. Generation builds from other clients'
sheets instead.

Agreed 2026-10-29:
- the benchmark is the client's template per group;
- groups are mapped to tabs in a table the team can correct;
- a tab that does not total 100 is refused with its total.

## B1. Loading

A benchmark is a workbook loaded for an exercise:
- **Web app:** uploaded on the Generation page.
- **CLI:** `generate --benchmark <file.xlsx> [--benchmark-map <map.csv>]`.

Each tab is read with the usual parser and belongs to the exercise's client,
whatever code or client name the tab carries. It is never added to the bank,
because it is the client's instruction for this run, not past history.

Each tab is checked:
- **Total.** It must be exactly 100 marks. A tab that is not is refused,
  for example "PSI Eng con (2): total 110, not used; fix the tab". The
  categories mapped to it are built from the bank, as today, with the same
  note.
- **Structure.** The template check's error rules apply (spec 03 C4, C6,
  C7). A tab with errors is refused the same way and lists them.

The load reports each tab as usable or refused, with the reason.

## B2. Mapping groups to tabs

A small table, `group | kind | tab`:
- **kind** is optional: goods, service, works or consultancy. It is set where
  one group needs two tabs, for example GENERAL goods and GENERAL services.
- **Proposed by the system** for each group in the categories table:
  1. a tab whose name shares a word with the group, ignoring the client's
     name and words like "and" or "services" (ICT → "PSI ICT");
  2. otherwise, the usable tab whose questions are most similar to the
     group's category descriptions;
  3. a group whose categories are of more than one kind gets one line per
     kind, each matched on that kind's categories.

  The proposal is a starting point. The team corrects it in the app or the
  CSV, and a corrected line is kept on later runs.
- **Unmapped.** A group with no line is built from the bank, as today, and
  the QA report says so.
- **Client-neutral.** No client's tab names or groups appear in code.

## B3. Generation

A category whose group (and kind) is mapped to a usable tab starts from that
tab:
- its questions, wording, order, answer types and marks per row;
- so its section split is the client's own.

Then everything else applies as usual:
- the category's sub-categories, eligibility, gate and Applies To;
- its licence, HR and technical items (spec 07 R4), for example HR roles
  replacing the tab's role rows and keeping their marks;
- the template rules (spec 02), for example the tab's grade rows in
  Registration moving to Eligibility;
- the brief (spec 07 B);
- the bank's category-specific suggestions, still offered with 0 marks.

The tab is the client's own choice for the group, so its rows are not
flagged as specific to another category.

The QA report names the tab each sheet was built from.

## Acceptance

On synthetic data:
- A benchmark with three tabs: one usable, one totalling 98, and one carrying
  another client's code.
  - The 98 tab is refused, with its total.
  - The coded tab is usable and belongs to the exercise's client.
- The proposed map matches a group to a tab by a shared word. Otherwise it
  matches by content, and it splits a mixed group by kind.
- A category in a mapped group is built from its tab:
  - every section's marks equal the tab's;
  - HR roles from the categories table take the tab's role rows and their
    marks;
  - the sheet passes the check.
- A category whose tab was refused, or whose group is unmapped, is built from
  the bank with a note.

On PSI, run by the team:
- each group starts from its PSI tab;
- PSI Marketing and PSI Eng con (2) are refused until their totals are fixed.

## As built (2026-10-29)

- **What refuses a tab.** Only marks that do not add up: a total other than
  100, or subtotals and the total row disagreeing with the rows. A row with
  no answer type no longer refuses a whole tab, as PSI General and PSI ICT
  would otherwise have been. Other findings are listed as warnings at load.
  - Generation now sets a missing answer type from the wording, with a note
    and the row flagged for review: "Yes/No" gives type 4, "attach" or
    "upload" gives type 5.
  - What remains is caught by the pre-write check, and the sheet is listed
    as NOT WRITTEN with the reason. On PSI, the Consultancy tab's reference
    blocks and its all-zero option scores do this.
- **The proposed map on PSI.** It matches the agreed mapping on 11 of 12
  lines. COMMUNICATION AND PRINTING SERVICES goes to PSI General, because
  PSI Marketing's questions say nothing about communication. Correct that
  line in the map.
- **Fixes found on the way.**
  - Trade reference blocks are compared by what their uploads are, not their
    wording (spec 03 C17).
  - Other clients' full names ("Norwegian Refugee Council") are swapped for
    the target's, as their codes already were.
