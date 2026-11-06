# Specs: AI Criteria Feedback (October 2026)

These specs turn the team's "AI Criteria Feedback" (11 items) into buildable
requirements. Each rule was agreed with the TenderSure team before it was
written down; where a rule changes what past client sheets did, the spec says
so.

Process: agree → spec (here) → small tasks → implement. Nothing is built
from these specs until the task list is reviewed.

| Spec | Flow | Feedback items |
|---|---|---|
| [01-category-refinement.md](01-category-refinement.md) | Draft category list → revised list for the client → final list | 9 |
| [02-criteria-generation.md](02-criteria-generation.md) | Final category list → one criteria sheet per category | 1, 2, 3, 4, 5, 7, 8, 10, 11 |
| [03-check.md](03-check.md) | A sheet edited by hand → list of problems | 6 |
| [04-bank-learning.md](04-bank-learning.md) | History → the learned names and Yes/No verdicts every build reuses | 5, 11 at scale |
| [05-research.md](05-research.md) | Technical names, questions, licences and document names: our data first, then the web | team request, 2026-10-29 |
| [06-web-app.md](06-web-app.md) | The browser UI for refinement and generation (Flask + TanStack), over a shared service layer | (team request, 2026-10-29) |
| [07-client-brief.md](07-client-brief.md) | The client's brief, read first by the model, and the draft's licence, HR and technical columns | (team request, 2026-10-29) |
| [08-client-benchmark.md](08-client-benchmark.md) | The client's sample criteria, compared with ours (compare only) | (PSI, 2026-10-29) |
| [09-split-and-merge.md](09-split-and-merge.md) | Split and merge categories in the refinement review | (team request, 2026-10-29) |
| [10-review-cockpit.md](10-review-cockpit.md) | The Decisions review, built for a person: triage, bulk answers, one Apply | (team request, 2026-10-30) |
| [11-finish.md](11-finish.md) | Finishing a run: ready to send, mark final, check an edited copy, add to the bank | (team request, 2026-10-30) |
| [12-client-rules.md](12-client-rules.md) | Client rules, country-first sources, and PSI's generation comments | (PSI feedback, 2026-10-30) |
| [13-shared-server.md](13-shared-server.md) | One cloud server on the tailnet holding the team's one bank: identity, write lock, deploy, backups | (team request, 2026-10-30) |
| [14-psi-version-2.md](14-psi-version-2.md) | PSI's Version 2 comments: built-in Applies To, technical items with marks, sub-section marks rule, wrapped rows | (PSI feedback, 2026-10-30) |
| [15-rules-as-checks.md](15-rules-as-checks.md) | An edited copy checked against the client's rules and the categories table's items | (team request, 2026-10-30) |
| [16-lessons-from-psi.md](16-lessons-from-psi.md) | What PSI taught us: rules checked every run, client profile, rules proposed from the client's files, calmer review, preview | (team request, 2026-10-30) |
| [17-psi-version-3.md](17-psi-version-3.md) | PSI's Version 3 comments: an `add` client rule, Trade License naming, sum formulas, importing the client's list | (PSI feedback, 2026-11-02) |
| [18-draft-in-tabs-and-previous-list.md](18-draft-in-tabs-and-previous-list.md) | A draft over several tabs read as one, sub-category lines, and the client's previous list as evidence | (Amref, 2026-11-04) |
| [19-amref-proposal-review.md](19-amref-proposal-review.md) | What Amref's first proposal showed: eligibility from the client's words only, previous tabs, supply vs repair, split wording, abbreviations | (Amref, 2026-11-04) |
| [20-render.md](20-render.md) | The team server on Render: one Docker service with a disk, a public URL behind one team password | (team request, 2026-11-06) |
| [21-comment-driven-list.md](21-comment-driven-list.md) | The category list shaped by the team's comments: the client's draft as written, comments in the margin read by the AI, checked, decided one at a time | (Amref test, 2026-11-06) |

The build order is in [TASKS.md](TASKS.md).

The three flows run in that order, but each runs on its own:

    draft list ──refine──▶ revised list ──client review──▶ final list + categories table
                                                                  │
                                                     generate ◀───┘
                                                                  │
                                                  team edits ◀────┘
                                                                  │
                                                     check ◀──────┘

## Sources

- `AI Criteria Feedback.pdf`: the team's 11 items.
- `Norwegian Refugee Council Criteria.xlsx` (NRC 2027–28): the new 21-column
  template (`Sample Criteria` tab), 50 sheets built on it, and the full
  category history (`NRC Categories` → `Revised and Add. Categories` →
  `Final Categories` → `Advert`).
- `Red Cross Categories 1st Draft.xlsx`: a 74-category draft, the first input
  for refinement.
- The bank (past ERCS, SNV and ILRI sheets): question content, short names,
  and who each question applies to.

## Decisions that apply to all three specs

- Everything generated so far is temporary. Output switches to the new
  template outright; the ERCS 2026 batch is regenerated. Old 14- and
  16-column sheets are still *read*, because the history is in them.
- Question content still comes from the client's past sheets. NRC supplies
  the layout only, and its Kenya-specific wording (PIN, CR12, KES) must not
  leak into other countries' sheets.
- The model proposes, the code applies and verifies (unchanged from SPEC.md).
  Every model call goes through the privacy layer.
- Learned facts that the team can correct live in plain CSV files under
  `data/` (glossary, question audience, yes/no polarity), so a correction is
  made once and reused.
