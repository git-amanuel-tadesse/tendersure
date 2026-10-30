# Spec 11: Finishing a run

When a run needs nothing (no open decision, no answer waiting to be
applied), the Generation page offered only "Apply answers → run 17": another
run that would change nothing. After generation, the work moves on:

1. the team sends the workbook, or edits it first;
2. an edited copy is checked (spec 03);
3. the result goes into the bank, so the client's own sheets are where the
   next exercise starts (spec 02 G1, spec 08).

## F1. Ready, or not

- **Ready.** A run is ready when it has no open decision and no answer saved
  that it has not applied.
  - The page shows a **Ready to send** panel in place of the review prompt:
    sheets written, nothing open, and the next steps.
  - **Apply answers** is disabled, with the label "nothing new to apply".
- **Not ready.** While a decision is open, or an answer is waiting, the
  review and Apply stay as they are.

## F2. The next steps

| Step | What it does |
|---|---|
| **Download** | The workbook and its QA report, as before. |
| **Mark as final** | Records this run as the exercise's final version (`final.json`: run, file, time). The runs list shows a **final** badge, and marking another run moves it. |
| **Check an edited copy** | Upload the workbook as the team edited it in Excel. It is checked against this run's workbook, so changes since generation are listed (spec 03 C16), and against the categories table. The result shows errors and warnings, and the report downloads. |
| **Add to the bank** | Ingests the edited copy if there is one, else this run's workbook, as history, so later exercises reuse the client's own sheets. It is ingested under the exercise's client. Sheets graded unusable are reported, not added. |

Every step runs as a job with progress shown. None of them changes the run.

## Acceptance

- A run with nothing open and nothing waiting shows *Ready to send*, and its
  Apply button reads "nothing new to apply".
- *Mark as final* gives that run the badge, and the exercise's
  `final.json` names it.
- An edited copy with one row deleted and one mark changed is checked: 0
  errors, and the two changes are listed against the run's workbook.
- *Add to the bank* on a run of a new client puts that client's sheets in
  the bank. A second add of the same file reports them unchanged.
