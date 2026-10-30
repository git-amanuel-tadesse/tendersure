"""Validation rules for TenderSure criteria sheets.

Two classes of rule, deliberately separated:

  PLATFORM  - things the upload will reject. Catching these locally removes
              upload round-trips. Confirm the real list with the TenderSure
              team; these are inferred.

  SILENT    - structurally valid files that behave wrongly for the supplier.
              The platform cannot reject these, so they reach people.

Every finding is advisory. Nothing here blocks: the odd case is sometimes
deliberate and the reviewer overrides.
"""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .schema import ANSWER_TYPES, TOTAL_MARKS, business_types
from .parser import Sheet, Row, normalise, section_key

PLATFORM = "platform"
SILENT = "silent"


@dataclass
class Finding:
    sheet: str
    excel_row: Optional[int]
    rule: str
    kind: str          # platform | silent
    severity: str      # error | warning
    message: str
    suggestion: str = ""

    def as_dict(self):
        return {
            "Sheet": self.sheet,
            "Row": self.excel_row,
            "Rule": self.rule,
            "Class": self.kind,
            "Severity": self.severity,
            "Problem": self.message,
            "Suggested fix": self.suggestion,
        }


ATTACH_WORDS = ("attach", "upload")


def _looks_like_attachment(text: str) -> bool:
    low = text.lower()
    return any(low.startswith(w) or f" {w}" in low[:40] for w in ATTACH_WORDS)


def check_answer_types(sheet: Sheet) -> List[Finding]:
    out = []
    for r in sheet.rows:
        t = r.answer_type
        if t is None:
            out.append(Finding(sheet.name, r.excel_row, "AT-MISSING", PLATFORM, "error",
                               "No answer type set.",
                               "Set an answer type between 1 and 5."))
            continue
        if t not in ANSWER_TYPES:
            out.append(Finding(sheet.name, r.excel_row, "AT-RANGE", PLATFORM, "error",
                               f"Answer type {t} is outside the valid range 1-5.",
                               "Use 1 text, 2 single select, 3 multi select, 4 yes/no, 5 attachment."))
            continue

        spec = ANSWER_TYPES[t]

        if spec["requires_options"] and not r.option_list():
            out.append(Finding(sheet.name, r.excel_row, "AT-NO-OPTIONS", PLATFORM, "error",
                               f"Answer type {t} ({spec['name']}) has no options listed.",
                               "Add the options, or change the answer type."))

        if not spec["requires_options"] and r.option_list():
            out.append(Finding(sheet.name, r.excel_row, "AT-STRAY-OPTIONS", SILENT, "warning",
                               f"Options are listed but answer type {t} ({spec['name']}) does not use them.",
                               "Change to type 2 or 3, or clear the options."))

        if t == 5 and not (r.attachment or r.compliance_attach):
            out.append(Finding(sheet.name, r.excel_row, "AT-NO-UPLOAD-FLAG", SILENT, "warning",
                               "Attachment question with neither the Attachment nor the Compliance flag set.",
                               "Set Compliance (M) if it carries compliance marks, otherwise Attachment (L)."))

        if _looks_like_attachment(r.criteria) and t != 5:
            out.append(Finding(sheet.name, r.excel_row, "AT-TEXT-SAYS-ATTACH", SILENT, "error",
                               f"Wording asks for a document but answer type is {t} ({spec['name']}). "
                               "The supplier gets the wrong control.",
                               "Set answer type 5."))

        if t == 5 and not _looks_like_attachment(r.criteria) and r.criteria.lower().startswith("insert"):
            out.append(Finding(sheet.name, r.excel_row, "AT-ASKS-TEXT", SILENT, "error",
                               "Wording asks for a typed value but answer type is 5 (upload).",
                               "Set answer type 1."))
    return out


def check_options(sheet: Sheet) -> List[Finding]:
    out = []
    for r in sheet.rows:
        opts, scores = r.option_list(), r.score_list()
        if opts and scores and len(opts) != len(scores):
            out.append(Finding(sheet.name, r.excel_row, "OPT-COUNT", PLATFORM, "error",
                               f"{len(opts)} options but {len(scores)} scores.",
                               "Give one score per option."))
        if opts and not scores:
            out.append(Finding(sheet.name, r.excel_row, "OPT-NO-SCORES", PLATFORM, "warning",
                               f"{len(opts)} options with no scores set.",
                               "Add a score per option, zeros if unscored."))
        try:
            values = [float(s) for s in scores]
        except ValueError:
            bad = next(s for s in scores if not _is_number(s))
            out.append(Finding(sheet.name, r.excel_row, "OPT-SCORE-NaN", PLATFORM, "error",
                               f"Option score '{bad}' is not a number.", "Use numbers only."))
            continue
        # 534 of 562 scored select rows in the corpus have best score == row
        # marks; the rest are either unreachable marks or uncounted scores.
        if values and r.answer_type in (2, 3) and abs(max(values) - (r.marks or 0)) > 1e-6:
            out.append(Finding(sheet.name, r.excel_row, "OPT-MAX-MARKS", SILENT, "error",
                               f"Best option scores {max(values):g} but the row is worth {r.marks or 0:g} marks. "
                               + ("No supplier can reach full marks."
                                  if max(values) < (r.marks or 0)
                                  else "The option scores points the row does not count."),
                               "Make the best option's score equal the row's marks."))
    return out


def _is_number(s) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def check_marks(sheet: Sheet) -> List[Finding]:
    """Subtotals must equal the rows above them; the total must equal 100."""
    out = []
    totals = [s for s in sheet.structural if "total marks" in s.label.lower()]
    subs = [s for s in sheet.structural if "total marks" not in s.label.lower()]

    for s in subs:
        if s.label == "<unlabelled>":
            out.append(Finding(sheet.name, s.excel_row, "SUM-NO-LABEL", SILENT, "warning",
                               "A subtotal row has no 'Sub Total' label in the Section column.",
                               "Add the label so the section is identifiable."))
        if s.marks is not None and s.system is not None and s.compliance is not None:
            if abs((s.system + s.compliance) - s.marks) > 1e-6:
                out.append(Finding(sheet.name, s.excel_row, "SUM-SPLIT", PLATFORM, "error",
                                   f"Subtotal {s.marks:g} does not equal system {s.system:g} "
                                   f"+ compliance {s.compliance:g}.",
                                   "Correct the split."))

    for xr in sheet.misplaced_labels:
        out.append(Finding(sheet.name, xr, "SUM-LABEL-COLUMN", PLATFORM, "warning",
                           "Sub Total / Total label is not in the Section column (B).",
                           "Move the label to column B; an importer may read the row as a question."))

    out.extend(_check_rows_against_subtotals(sheet))

    if not totals:
        out.append(Finding(sheet.name, None, "SUM-NO-TOTAL", PLATFORM, "error",
                           "No 'Total Marks' row found.", "Add one."))
        return out

    t = totals[-1]
    if t.marks is not None and t.system is not None and t.compliance is not None:
        if abs((t.system + t.compliance) - t.marks) > 1e-6:
            out.append(Finding(sheet.name, t.excel_row, "SUM-TOTAL-SPLIT", PLATFORM, "error",
                               f"Total {t.marks:g} does not equal system {t.system:g} "
                               f"+ compliance {t.compliance:g}.",
                               "A row is probably missing from one of the System/Compliance sums."))
    if t.marks is not None and abs(t.marks - TOTAL_MARKS) > 1e-6:
        out.append(Finding(sheet.name, t.excel_row, "SUM-NOT-100", PLATFORM, "error",
                           f"Total marks is {t.marks:g}, not {TOTAL_MARKS}.",
                           "Rebalance the section marks."))

    sub_sum = sum(s.marks for s in subs if s.marks is not None)
    if t.marks is not None and subs and abs(sub_sum - t.marks) > 1e-6:
        out.append(Finding(sheet.name, t.excel_row, "SUM-MISMATCH", PLATFORM, "error",
                           f"Subtotals add to {sub_sum:g} but the total says {t.marks:g}.",
                           "Check each section subtotal."))
    return out


def _check_rows_against_subtotals(sheet: Sheet) -> List[Finding]:
    """Each subtotal must equal the question rows since the previous one."""
    out = []
    items = sorted([(r.excel_row, r) for r in sheet.rows] +
                   [(s.excel_row, s) for s in sheet.structural], key=lambda t: t[0])
    running = 0.0
    every = 0.0
    for xr, obj in items:
        if isinstance(obj, Row):
            running += obj.marks or 0
            every += obj.marks or 0
            continue
        is_total = "total marks" in obj.label.lower()
        expected = every if is_total else running
        if obj.marks is not None and abs(obj.marks - expected) > 1e-6:
            out.append(Finding(sheet.name, xr, "SUM-ROWS", PLATFORM, "error",
                               f"{'Total' if is_total else 'Subtotal'} reads {obj.marks:g} but the "
                               f"question rows {'in the sheet' if is_total else 'above it'} add to {expected:g}.",
                               "Check the SUM range and any row marks typed in by hand."))
        if not is_total:
            running = 0.0
    return out


def check_text(sheet: Sheet) -> List[Finding]:
    out = []
    for r in sheet.rows:
        c = r.criteria
        if "  ?" in c or c.rstrip().endswith("of ?") or c.rstrip().endswith("of  ?"):
            out.append(Finding(sheet.name, r.excel_row, "TXT-PLACEHOLDER", SILENT, "error",
                               "Looks like a client name was never filled in when this sheet was copied.",
                               "Insert the client name."))
        if "\n" in c:
            out.append(Finding(sheet.name, r.excel_row, "TXT-LINEBREAK", SILENT, "warning",
                               "Question text contains a line break.",
                               "Remove it; some importers split on newlines."))
    return out


def check_both_upload_flags(sheet: Sheet) -> List[Finding]:
    out = []
    for r in sheet.rows:
        if r.attachment and r.compliance_attach:
            out.append(Finding(sheet.name, r.excel_row, "UP-BOTH-FLAGS", SILENT, "warning",
                               "Both the Attachment (L) and Compliance (M) upload flags are set.",
                               "Set only one: M when the upload carries compliance marks, otherwise L."))
        if r.compliance_attach and r.compliance is None and (r.marks or 0) > 0:
            out.append(Finding(sheet.name, r.excel_row, "UP-COMP-NO-MARKS", SILENT, "warning",
                               "Flagged as a compliance upload but no compliance marks recorded.",
                               "Put the marks in the Compliance column, or use the Attachment flag."))
    return out


def check_identity(sheet: Sheet) -> List[Finding]:
    if sheet.client:
        return []
    return [Finding(sheet.name, 2, "ID-NO-CLIENT", SILENT, "warning",
                    f"Title '{sheet.title[:40]}' has no client prefix, so the client is unknown.",
                    "Restore the client prefix, e.g. 'SNV-ET-2026_016'.")]


ALL_CHECKS = [
    check_answer_types,
    check_options,
    check_marks,
    check_text,
    check_both_upload_flags,
    check_identity,
]


def validate_sheet(sheet: Sheet) -> List[Finding]:
    out = []
    for fn in ALL_CHECKS:
        out.extend(fn(sheet))
    return sorted(out, key=lambda f: (f.excel_row or 0, f.rule))


# --- template rules (specs/02) ------------------------------------------------
# Rules the new template adds. They hold for generated and edited sheets, not
# for the history: past sheets predate them, and counting them against a sheet
# would change its trust grade. Each rule is a check (find) and a fix that
# generation applies, kept side by side so the two cannot drift.

def marks_column(r: Row) -> str:
    """Where a row's marks go when neither System nor Compliance holds them:
    Compliance for uploads and flagged rows, System otherwise (spec 02 G4)."""
    if r.answer_type == 5 or r.attachment or r.compliance_attach or r.eligibility:
        return "compliance"
    return "system"


def _marks_columns_finding(sheet: Sheet, r: Row) -> Optional[Finding]:
    if r.marks is None or (r.system is None and r.compliance is None):
        col = marks_column(r).title()
        shown = "blank" if r.marks is None else f"{r.marks:g}"
        return Finding(sheet.name, r.excel_row, "MARKS-ONE-COLUMN", SILENT, "error",
                       f"Marks {shown} is not repeated in System or Compliance.",
                       f"Write {0 if r.marks is None else f'{r.marks:g}'} in Marks and in {col}.")
    if abs((r.marks or 0) - (r.system or 0) - (r.compliance or 0)) > 1e-6:
        return Finding(sheet.name, r.excel_row, "MARKS-SPLIT", PLATFORM, "error",
                       f"Marks {r.marks:g} but System {r.system or 0:g} + Compliance {r.compliance or 0:g}.",
                       "Make Marks equal System + Compliance.")
    return None


def check_marks_columns(sheet: Sheet) -> List[Finding]:
    """Spec 02 G4: every row repeats its marks in System or Compliance, and
    Marks = System + Compliance."""
    return [f for f in (_marks_columns_finding(sheet, r) for r in sheet.rows) if f]


def fix_marks_columns(r: Row) -> bool:
    """Blank Marks becomes 0; Marks is repeated in System or Compliance."""
    changed = False
    if r.marks is None:
        r.marks, changed = (r.system or 0) + (r.compliance or 0), True
    if r.system is None and r.compliance is None:
        setattr(r, marks_column(r), r.marks)
        changed = True
    return changed


def fix_upload_flags(r: Row) -> Optional[str]:
    """The fix for UP-BOTH-FLAGS and UP-COMP-NO-MARKS: where an upload's marks
    sit decides its flag - M (Compliance) when they are compliance marks, L
    (Attachment) otherwise. The marks themselves do not move, so no total
    changes. An Eligibility upload is always M (spec 02 G3). Returns what changed."""
    if r.eligibility:
        if r.attachment or not r.compliance_attach:
            r.attachment, r.compliance_attach = False, True
            return "upload flag set to Compliance (M): an Eligibility document"
        return None
    if r.attachment and r.compliance_attach:
        keep_m = bool(r.compliance)
        r.attachment, r.compliance_attach = not keep_m, keep_m
        return f"both upload flags set -> kept {'Compliance (M)' if keep_m else 'Attachment (L)'}: where its marks are"
    if r.compliance_attach and r.compliance is None and (r.marks or 0) > 0:
        r.attachment, r.compliance_attach = True, False
        return "upload flag M -> L: its marks are System marks, not compliance marks"
    return None


def fix_stray_options(r: Row) -> Optional[str]:
    """The fix for AT-STRAY-OPTIONS: an answer type that takes no options
    (text, Yes/No, upload) keeps none."""
    spec = ANSWER_TYPES.get(r.answer_type)
    if not spec or spec["requires_options"] or not r.option_list():
        return None
    old, r.options, r.option_scores = r.options, None, None
    return f"options '{old}' cleared: answer type {r.answer_type} takes none"


def fix_option_scores(r: Row) -> Optional[str]:
    """Spec 02 G9, the fix for OPT-MAX-MARKS: scale a select row's option
    scores so its best option is worth the row's marks (0 marks and scores
    1,2,3 become 0,0,0). Returns what changed. Scores that are not numbers,
    or all 0 on a scored row (no option is known to be best), are left for a
    person; the check reports them."""
    scores = r.score_list()
    if r.answer_type not in (2, 3) or not scores or not all(_is_number(s) for s in scores):
        return None
    values, marks = [float(s) for s in scores], r.marks or 0
    best = max(values)
    if abs(best - marks) < 1e-6 or best <= 0:
        return None
    old = r.option_scores
    r.option_scores = ",".join(f"{marks if v == best else round(v * marks / best):g}" for v in values)
    return f"option scores {old} -> {r.option_scores}: the best option now scores the row's {marks:g} marks"


YES_GOOD, YES_BAD = "good", "bad"


def is_yes_no(r: Row) -> bool:
    """A Yes/No toggle (type 4), or a single select whose options are Yes and No."""
    return r.answer_type == 4 or (r.answer_type == 2 and sorted(o.lower() for o in r.option_list()) == ["no", "yes"])


def yes_no_target(yes_is: Optional[str], marks: float) -> Optional[Tuple[int, Optional[str], Optional[str]]]:
    """Spec 02 G10 (feedback item 11): Yes is good, or the row has no marks
    -> type 4, no options; Yes is bad and the row has marks -> type 2,
    options Yes,No, scores 0,<marks>. None while the verdict is unknown."""
    if marks <= 0 or yes_is == YES_GOOD:
        return 4, None, None
    if yes_is == YES_BAD:
        return 2, "Yes,No", f"0,{marks:g}"
    return None


def _yes_no_finding(sheet: Sheet, r: Row, verdict: Optional[str]) -> Optional[Finding]:
    marks = r.marks or 0
    want = yes_no_target(verdict, marks)
    if want is None:
        return Finding(sheet.name, r.excel_row, "YESNO-UNKNOWN", SILENT, "warning",
                       "Scored Yes/No question: nobody has said whether Yes is the good answer.",
                       "Set yes_is to good or bad in data/yesno_polarity.csv.")
    scores = [float(s) for s in r.score_list() if _is_number(s)]
    got = (r.answer_type, [o.lower() for o in r.option_list()], scores)
    exp = (want[0], ["yes", "no"] if want[1] else [], [0.0, marks] if want[2] else [])
    if got == exp:
        return None
    how = "type 4 (Yes/No)" if want[0] == 4 else f"type 2, options Yes,No scored 0,{marks:g}"
    return Finding(sheet.name, r.excel_row, "YESNO-TYPE", SILENT, "error",
                   f"Yes is {verdict} here{'' if marks > 0 else ' (no marks)'}, so the question is {how}.",
                   f"Set it to {how}.")


def check_yes_no(sheet: Sheet, yes_is: Callable[[List[Row], int], Optional[str]]) -> List[Finding]:
    """Spec 02 G10. `yes_is(rows, i)` gives the team's verdict on row i
    ('good', 'bad' or None)."""
    out = []
    for i, r in enumerate(sheet.rows):
        if is_yes_no(r):
            f = _yes_no_finding(sheet, r, yes_is(sheet.rows, i) if (r.marks or 0) > 0 else YES_GOOD)
            if f:
                out.append(f)
    return out


def check_gate(sheet: Sheet, gate: bool, applies_to: List[str]) -> List[Finding]:
    """Spec 02 G7: with a gate, one gate question whose options are the
    category's business types, and Applies To values among them; without
    one, both columns empty."""
    out = []
    gates = [r for r in sheet.rows if r.gate_question]
    used = [r for r in sheet.rows if r.applies_to]
    if not gate:
        return [Finding(sheet.name, r.excel_row, "GATE-UNEXPECTED", SILENT, "error",
                        "Gate Question or Applies To set, but the category has no gate.",
                        "Clear both columns.") for r in gates + [r for r in used if r not in gates]]
    types = business_types(applies_to)
    if len(gates) != 1:
        out.append(Finding(sheet.name, gates[1].excel_row if len(gates) > 1 else None, "GATE-ROWS", SILENT,
                           "error", f"{len(gates)} gate questions; the category needs exactly one.",
                           "Set Gate Question TRUE on the Organization type question only."))
    for g in gates[:1]:
        if business_types(g.options) != types:
            out.append(Finding(sheet.name, g.excel_row, "GATE-OPTIONS", SILENT, "error",
                               f"Gate options '{g.options}' are not the category's business types.",
                               f"Set them to '{', '.join(types)}'."))
    for r in used:
        stray = [t for t in business_types(r.applies_to) if t not in types]
        if stray:
            out.append(Finding(sheet.name, r.excel_row, "GATE-APPLIES", SILENT, "error",
                               f"Applies To names {', '.join(stray)}, not an option of the gate question.",
                               "Use only the gate question's options."))
    return out


# The Trade License - Kenya's is the (single) business permit.
TRADE_LICENSE = re.compile(r"trade\s*licen[cs]e|business\s*permit", re.I)


def asks_for_document(r: Row) -> bool:
    """An upload, or a question whose wording asks for one ("Please attach",
    "If yes attach a copy"); not a typed value ("Insert ... number")."""
    low = r.criteria.lower().strip()
    if low.startswith(("insert", "enter", "provide the number", "state")):
        return False
    return r.answer_type == 5 or bool(re.search(r"\b(attach|upload)\b", low))


def check_eligibility(sheet: Sheet) -> List[Finding]:
    """Spec 02 G3: the documents in the Eligibility section are mandatory,
    compliance-flagged uploads marked Eligibility; nothing else is marked; the
    section holds the Trade License."""
    out = []
    in_section = [r for r in sheet.rows if section_key(r.section) == "eligibility"]
    ids = {id(r) for r in in_section if asks_for_document(r)}
    for r in sheet.rows:
        if id(r) in ids and not (r.eligibility and r.answer_type == 5 and r.mandatory and r.compliance_attach):
            out.append(Finding(sheet.name, r.excel_row, "ELIG-ROW", SILENT, "error",
                               "Row in the Eligibility section is not a mandatory, compliance-flagged upload "
                               "marked Eligibility.",
                               "Set Eligibility TRUE, Answer Type 5, Mandatory TRUE, Compliance TRUE."))
        elif id(r) not in ids and r.eligibility:
            out.append(Finding(sheet.name, r.excel_row, "ELIG-OUTSIDE", SILENT, "error",
                               f"Eligibility is TRUE on a row that is not a document in the Eligibility section "
                               f"('{r.section}').",
                               "Clear Eligibility, or move the document to the Eligibility section."))
    if not any(TRADE_LICENSE.search(r.criteria) for r in in_section):
        out.append(Finding(sheet.name, None, "ELIG-NO-TRADE-LICENSE", SILENT, "error",
                           "The Eligibility section does not ask for the Trade License.",
                           "Add 'Please attach a valid Trade license' to the Eligibility section."))
    return out


def needs_names(r: Row) -> bool:
    """Rows that must carry a Short Name and Acronym: uploads, and rows flagged
    Attachment, Compliance or Eligibility (spec 02 G8)."""
    return r.answer_type == 5 or r.attachment or r.compliance_attach or r.eligibility


def check_names(sheet: Sheet) -> List[Finding]:
    """Spec 02 G8: required rows have both names, any other row with a Short
    Name has an acronym too, and acronyms are unique in a sheet."""
    out, first = [], {}
    for r in sheet.rows:
        if not needs_names(r):
            if r.archive_short_name and not r.acronym:
                out.append(Finding(sheet.name, r.excel_row, "NAMES-NO-ACRONYM", SILENT, "warning",
                                   f"Short Name '{r.archive_short_name}' has no Acronym.",
                                   "Add the acronym (the letters of the Short Name, e.g. Company Name -> CN)."))
            continue
        missing = [n for n, v in (("Archive Short Name", r.archive_short_name), ("Acronym", r.acronym)) if not v]
        if missing:
            out.append(Finding(sheet.name, r.excel_row, "NAMES-MISSING", SILENT, "error",
                               f"Upload or flagged row without {' and '.join(missing)}.",
                               "Take the names from data/glossary.csv, or add them there."))
        elif r.acronym in first:
            out.append(Finding(sheet.name, r.excel_row, "NAMES-DUP-ACRONYM", SILENT, "error",
                               f"Acronym '{r.acronym}' is also used on row {first[r.acronym]}.",
                               "Give each document its own acronym (number repeated ones: REF 1, REF 2)."))
        else:
            first[r.acronym] = r.excel_row
    return out


TEMPLATE_CHECKS = [check_marks_columns, check_eligibility, check_names]

SUB_CATEGORY_QUESTION = "Please select the subcategory you would like to participate in"
SUB_CATEGORY = re.compile(r"select\s+the\s+sub-?\s*categor", re.I)


def sub_category_options(sub_categories: List[str]) -> str:
    """The Options cell for a category's sub-categories (spec 02 G5). A comma
    inside one name would split it into two options, so it becomes ' / '."""
    return ", ".join(" / ".join(part.strip() for part in s.split(",")) for s in sub_categories)


def check_sub_category_question(sheet: Sheet, sub_categories: List[str]) -> List[Finding]:
    """Spec 02 G5: one multi-select question listing the category's own
    sub-categories, scored 0 - and none when the category has none."""
    rows = [r for r in sheet.rows if SUB_CATEGORY.search(r.criteria)]
    if not sub_categories:
        return [Finding(sheet.name, r.excel_row, "SUBCAT-UNEXPECTED", SILENT, "error",
                        "Sub-category question, but the advert gives this category no sub-categories.",
                        "Remove the question.") for r in rows]
    if not rows:
        return [Finding(sheet.name, None, "SUBCAT-MISSING", SILENT, "error",
                        f"The advert lists {len(sub_categories)} sub-categories but no question asks for them.",
                        f"Add '{SUB_CATEGORY_QUESTION}' after the Organization type question.")]
    out = [Finding(sheet.name, r.excel_row, "SUBCAT-REPEATED", SILENT, "error",
                   "Second sub-category question.", "Keep one.") for r in rows[1:]]
    r, want = rows[0], sub_category_options(sub_categories)
    if r.option_list() != [o.strip() for o in want.split(",")]:
        out.append(Finding(sheet.name, r.excel_row, "SUBCAT-OPTIONS", SILENT, "error",
                           f"Options '{r.options}' are not this category's sub-categories.",
                           f"Set Options to '{want}'."))
    scores = r.score_list()
    if r.answer_type != 3 or not r.mandatory or len(scores) != len(sub_categories) \
            or any(not _is_number(s) or float(s) for s in scores):
        out.append(Finding(sheet.name, r.excel_row, "SUBCAT-FIELDS", SILENT, "error",
                           "Sub-category question must be multi-select (type 3), mandatory, every option scored 0.",
                           "Set Answer Type 3, Mandatory TRUE, one 0 per option."))
    return out


def check_template(sheet: Sheet) -> List[Finding]:
    out = [f for fn in TEMPLATE_CHECKS for f in fn(sheet)]
    return sorted(out, key=lambda f: (f.excel_row or 0, f.rule))


def _signature(sheet: Sheet):
    return [(r.criteria, r.marks, r.answer_type, r.options) for r in sheet.rows]


def check_corpus(sheets: List[Sheet], threshold: float = 0.95) -> List[Finding]:
    """Cross-sheet checks: near-identical sheets double-count in the bank.

    Same-client sheets for different categories are legitimately ~95%
    identical; a draft shares the category description too. Sheets are
    grouped by description first, so this stays fast on years of history.
    """
    out = []
    groups = {}
    for s in sheets:
        groups.setdefault(normalise(s.description or ""), []).append(s)
    for group in groups.values():
        if len(group) < 2:
            continue
        sigs = [(s, _signature(s)) for s in group]
        for i, (a, sa) in enumerate(sigs):
            for b, sb in sigs[i + 1:]:
                if not sa or abs(len(sa) - len(sb)) > 2:
                    continue
                same = sum(1 for x, y in zip(sa, sb) if x == y)
                if same / max(len(sa), len(sb)) >= threshold:
                    out.append(Finding(b.name, None, "SHEET-NEAR-DUP", SILENT, "warning",
                                       f"{same}/{max(len(sa), len(sb))} rows identical to "
                                       f"{a.name} ({a.source_file}). Both count toward bank frequencies.",
                                       "Keep the final version only, or exclude the draft from ingest."))
    return out
