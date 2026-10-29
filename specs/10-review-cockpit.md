# Spec 10: The Decisions review, built for a person

A PSI run has 335 decisions, and the model and the AI review have answered
nearly all of them. A person should spend their time on the few that need
them, see a sheet the way they check it, and answer many alike in one go.

Agreed 2026-10-30: filters, bulk answers and a per-sheet view, made as easy
to work as we can.

## R1. Triage first

Tabs above the list, each with its count. The page opens on the first tab
that is not empty.

| Tab | What it holds |
|---|---|
| **Needs you** | No answer and no status; or a status saying NOT APPLIED or PROBLEM. |
| **AI disagreed** | The AI review answered against the proposal, or changed it. Its status carries the reviewer's notes ("removal not applied, reviewer: …"). |
| **Marks vs client** | The M-BENCH lines: our split against the client's benchmark tab. |
| **Changed by you** | Answers edited and not yet applied. |
| **All** | Everything. |

A search box (question text, proposal, reason) and a **sheet picker** narrow
any tab.

## R2. A sheet at a time

Picking a sheet in the sheet picker, e.g. 023 Supply of Relief Non-Food
Items, shows only the decisions that touch it, with its description. It also
shows:
- that sheet's NOT WRITTEN reason, if it has one;
- its M-BENCH comparison, first.

The previous and next buttons step through the sheets.

## R3. Bulk answers for what is on screen

A bar above the list acts on the cards shown:
- **Take the client's split** on every M-BENCH line shown;
- **Keep ours**, which clears those answers;
- **Accept as proposed**, which clears answers in the view, the AI's
  included;
- **Undo my changes**, which drops the edits not yet saved.

Each says how many cards it changes, and nothing is saved until **Save**.

## R4. Calm cards

- **Collapsed when nothing is needed.** A card the AI agreed with shows as
  one line: the question, its sheets and "AI agreed". A click opens it.
- **The reason first.** A card the AI disagreed with shows the reviewer's
  reason above the controls.
- **M-BENCH side by side.** Each section's marks are shown as ours, the
  client's and the difference. Three buttons: *Keep ours*, *Take client's*,
  *Edit*.
- **Statuses in plain words**, e.g. "AI agreed · applied", "AI disagreed:
  …", "Not applied: …".

## R5. Always in reach

- The Save and Apply bar stays at the top while scrolling, with the count of
  changes.
- Keyboard: `j` / `k` move between cards, `o` opens or closes one.
- A small summary shows what is left: "Needs you 5 · AI disagreed 84 ·
  Marks vs client 69".

## Acceptance

- Opening run 2 lands on *Needs you*, with its count.
- The sheet picker on 023 lists only the decisions whose sheets include 023.
- *Take the client's split* on the *Marks vs client* tab sets `client` on
  every card shown, and Save stores them. Applying gives `client` on those
  lines, the same as answering one by one.
- AI-agreed cards are collapsed; disagreed ones show the reviewer's reason.
- Vitest covers the triage and the M-BENCH parsing.
