# Spec 21: The category list, shaped by the team's comments

## Why

Every piece of feedback the team has given has been an instruction:
- "the wordings should be as is";
- "take the questions from the Ethiopian jobs";
- "use the new categories";
- "LPO should be PO except engineering".

We turned each instruction into a feature, and the next client brought a new
one. For Amref, our proposal of 88 categories looked wrong to the team, and
they built their own list of 62 by hand. They started from PSI's approved
list, kept Amref's wording, and added nothing.

In a prototype (2026-11-06), comments in the team's own style took our list to
63 categories. Against the team's list:

| | Same group | Missing | Extra |
|---|---|---|---|
| Our proposal | 7 | 10 | 36 |
| After the comments | 55 | 6 | 7 |

So the team should be able to write comments, as they write feedback, and have
the system do the data work.

## Decisions (2026-11-06)

- **Comments, not chat.** A comment reads like the team's feedback: "we want
  this wording", "take it from PSI", "for all categories except Medical".
- **In the app, for every team member.**
- **The category list first.** Criteria sheets come later.
- **Layout A.** The list looks like the client deliverable, and comments sit
  in the margin, as in a document.
- **The list starts from the client's draft exactly as written.** There is no
  AI pass first: all shaping comes from comments.

## A. The list

- **The start.** The list is the client's draft as read: tabs as one draft,
  groups, and sub-category lines (spec 18 A–C).
  - The wording is as written: no title case, no rewording.
  - Each line keeps the draft rows it comes from.
- **What it looks like.** The client's layout: group headings, then `#`,
  Category Description and Sub-category.
  - Each line shows its draft rows in small print.
  - The count of categories and groups is always shown.
- **Saved on the server.** Every applied comment makes a new version of the
  list (`refine/list.json`, with `refine/versions/`). Reloading the page loses
  nothing.

## B. A comment

- **Writing.** Any team member writes a comment in the margin. The author and
  time are kept.
- **What the AI sees:**
  - the list now;
  - the client's draft as written;
  - the client's brief;
  - the team's approved lists for other clients, as sources a comment may name
    ("PSI's wording", "PSI's groups").

  Every call goes through the privacy layer.
- **One comment, several instructions.** A comment may hold several
  instructions. The answer lists each one with a status:
  - **done**;
  - **nothing to change**;
  - **partly done**;
  - **not understood**, with a note saying what the AI needs.

  Each instruction's changes are shown under it.
- **Actions.** A change is one of: reword, move to a group, remove, fold
  (becomes a sub-category of another line), merge (several lines become one,
  keeping all their draft rows and sub-categories), split, add, set
  sub-categories, or put the groups in order.
- **Every change says where its wording came from.** It shows the source
  ("Amref's draft", "PSI's list") and the exact line copied.
- **The code checks the answer before anyone sees it:**
  - **Rows exist.** Every row a change names must exist; any other change is
    reported, not shown.
  - **Nothing left out.** For each instruction, the AI names the lines it means
    to change. A named line without a change, or an instruction marked done
    with no changes, is asked again on its own, once. If it is still missing,
    the instruction shows as *partly done* or *not understood*, never as done.
    The prototype saw both: "done" with no changes, and done for 1 of 6 named
    categories.
  - **Merges have wording.** A merge with no wording keeps the first line's,
    with a note.

## C. Flags

A flag means "look before applying". **A flagged change starts unticked.** The
flags are kept few: when everything is flagged, people either ignore flags or
leave good changes out, and the prototype saw both.

| Flag | When | Not when |
|---|---|---|
| Wording not in a source | The new wording is not in the draft or any approved list, word for word | The comment gives the wording |
| Changes what the category covers | The old and new wording share less than two thirds of their words (plurals and spelling aside) | The line goes back to its own draft wording, or the comment names the new wording |
| Undoes a merge | A merged line is reworded to one of its own draft rows | |

A removal the comment asks for is never flagged.

## D. Deciding

- **Apply or Reject.** *Apply* applies the ticked changes and makes a new
  version of the list. *Reject* keeps the list as it is.
- **One comment at a time per exercise.** While a comment waits for a
  decision, a new one is refused with "decide on the last comment first", and
  its text is kept.
- **If the list changed meanwhile** (another team member applied a comment
  first), the waiting comment is read again on the new list before anyone can
  apply it.
- **Undo.** The latest applied comment can be undone: the list goes back to
  the version before it.

## E. "Always, for this client" (second phase)

- **Kept for the client.** A comment marked *always* is kept as a standing
  comment for the client.
- **Offered again.** On the client's next exercise, after the draft is read,
  each standing comment is offered in order. Each is read by the AI against
  the new list and decided as usual.

This was not tested in the prototype; build it after A–D are in use.

## F. The record and the check

- **The log.** Every comment, the AI's answer as it came, the decision with
  each change ticked or not, and each version are kept in
  `refine/comments.jsonl`.
- **"Check against a list".** This is a closed panel. It compares the list
  with a list the team made, e.g. a past exercise's final list. It shows:
  - same wording, close wording and same group;
  - what is missing and what is extra.

  It is for testing the system on past exercises, as with Amref.

## G. Outputs

- **For the client.** A workbook in the client's layout: group rows, `#`,
  Category Description, and sub-categories one per row beside their category.
- **For criteria.** The categories table: one category per line, with its
  group, sub-categories and draft rows. Gate Question and Applies To come
  from the rules, as today.

## H. What leaves the page

These leave the Refinement page:
- the AI proposal (*Propose*);
- the Decisions review table;
- the previous-list card (spec 18 D);
- the 26-column internal review workbook.

Their code is removed in a later task (the build phase keeps no old paths).
The draft reader of spec 18 A–C stays. The prototype (`?variant=` A/B/C)
moves to a throwaway branch.

## Out of scope

- Comments on criteria sheets: later, once the list works.
- Conversation with the AI: comments only.

## Open questions

1. **Which lists count as "approved"?** Every other exercise's categories
   table, or only exercises marked final (spec 11)?
2. **Undo:** is undoing only the latest comment enough at first?
3. **Two team members at once:** is "the first decided wins, the other is
   read again" right?

## Acceptance

- **Amref, from its draft as written.** The team-style comments from the
  prototype test end with:
  - the same group for at least 55 of the team's 62 categories;
  - at most 6 missing;
  - at most 7 extra.

  Each instruction is shown done, partly done or not understood, never
  wrongly done.
- **Checks.** On recorded answers from the prototype log:
  - "done with no changes" and "done for 1 of 6 named lines" are both caught;
  - the flags fire on the bad PSI swaps (cleaning, water treatment, events,
    tyres, translation) and not on the same run's correct changes.
- **One at a time.** A second comment before a decision is refused, and its
  text is kept.
- **Lasting.** Reloading the page keeps the list and its comments, and undo
  restores the version before the last comment.
- **Outputs.** The client workbook has the team's layout; the categories table
  is accepted by generation.

## As built (2026-11-06)

T127–T130 are built. T131 is built except the old core and CLI refinement.

**The open questions, answered by default (no answer came):**
1. **Approved lists** are every other exercise's categories table, plus `data/*_categories.csv`.
2. **Undo** covers the latest applied comment only.
3. **Two team members at once:** the first decision wins; a comment read on an older version must be read again.

**Where it is:**
- **Core:** `tendersure/catlist.py` (the list, applying changes, reading a comment, the checks, comparison, outputs)
  and `flows/catlist.py`.
- **API:** `tendersure_api/list_api.py` (`/refine/list`, `/comment`, `/reread`, `/decide`, `/undo`, `/check`,
  `/client.xlsx`, `/categories`). `refine_api.py` keeps only reading the draft, which restarts the list.
- **Web:** the Refinement page (`components/list/`).
- **Categories:** the categories page imports from the list (`source: "list"`).

**Details the spec left open:**
- **Targets.** Each instruction names its targets: the rows it changes. Code compares them with the changes
  that came back. Done with no change, or with targets left, is asked again once, then shown as partly done or
  not understood.
- **The author** is a name typed once in the margin and kept in the browser. The team server has one shared
  password, so there is no login identity.
- **A list for an exercise** whose draft was read before spec 21 is started from that draft when the page
  first opens it.

**Removed:**
- The page's AI proposal, Decisions table, previous-list card and review workbooks.
- Their API routes, and the categories page's import from the applied review.
- The prototype, which is copied with its notes to `reports/prototype-comment-list/` (not committed).

**Not removed yet:**
- The old core refinement (`refine.py`'s proposal, review and apply) and the CLI's `tendersure refine`, which
  still use them.
- The previous-list reader (`previous.py`), used by `refine --previous`.

They go once the CLI either moves to the list or drops refinement.

**Checked:**
- 360 Python tests, including `test_catlist.py` and `test_list.py`, plus import rules, contract and web tests.
- The page renders Amref's 94 rows as written.
- One real comment on Amref ("put rapporteur under events; split stationery as PSI does") came back in 8 s:
  both instructions done with their targets, a fold and PSI's split, nothing flagged.

## Acceptance run on Amref (T133, 2026-11-06)

A team member started from Amref's draft as written and wrote 7 comments in the team's style. The list was
scored against the team's own *Amref Categories Final Draft* (62 categories):

| Version | After | Categories | Same wording | Same group | Missing | Extra |
|---|---|---|---|---|---|---|
| v1 | the draft as written | 94 | 30 | 9 | 10 | 42 |
| v2 | Project Based Consultancy and Capacity Building umbrellas | 82 | 32 | 9 | 9 | 29 |
| v3 | merges (only 2 of 13 came back: fixed, below) | 73 | 32 | 9 | 9 | 20 |
| v4 | splits, adds, removes | 74 | 37 | 10 | 6 | 18 |
| v5 | wording | 74 | 44 | 10 | 5 | 17 |
| v6 | PSI's groups, the engineering heading, moves | 74 | 44 | 55 | 5 | 17 |
| v7 | the merges again, after the fix: 11 of 11 done | 61 | 46 | 55 | 6 | 5 |
| v8 | clean-up | **61** | **46** | **55** | **6** | **5** |

**Passed:** the same group for 55 of 62, 6 missing, 5 extra. Every instruction was shown done, partly done or
not understood, never wrongly done.

**Fixed after the run:**
- **A long answer stopped early.** The model named all 13 instructions and their rows, but returned one change;
  asked again for the other 12 together, it again returned one. Missing instructions are now asked one at a
  time, four at once.
- **The last five wordings missed:**
  - **Two changes rewording the same line in one comment.** The later one now carries a flag and starts
    unticked, so "brackets to round" can no longer undo "use PSI's wording" (Relief NFI, branded
    promotional).
  - **The wrong name for a merge that came back without wording.** It now takes the name the instruction
    gives the result (after "into", "to", "as", "using" or "category:"), expanded to the whole source line it
    begins. That fixes audio-visual, animation and software.
- **The list drops a list number left in a numbered row's text** ("4 Provision of …"), as the draft reader does
  (spec 18 A).

**Not fixed:** "start consultancy categories with 'Provision of'" was also applied to "Production of Radio and
TV…". That is the model's reading, and it can be undone by a comment.
