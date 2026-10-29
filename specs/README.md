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
