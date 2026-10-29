"""Grade past sheets automatically, so years of history need no human QA.

Past sheets are evidence, not deliverables. An error in one matters only if
it is copied into a new sheet, so instead of asking people to review the
history, every sheet gets a tier at ingest:

  clean         no findings that change behaviour
  correctable   has errors code fixes on reuse (attach-as-text, etc.)
  quarantined   broken arithmetic or identity; never a skeleton, donor or
                marks source (its wording still counts in the bank)
  superseded    an earlier copy of another sheet; ignored for reuse and
                for frequencies

Within one client, a structural field that most of that client's sheets
agree on (CONVENTION_RULES) is the client's convention; a copied row that disagrees is a copy
error and is corrected on reuse. Real disagreements are left for a person,
but only when they reach a sheet being generated (see review.py).
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import Row, Sheet, normalise
from .validate import check_corpus, validate_sheet

CLEAN, CORRECTABLE, QUARANTINED, SUPERSEDED = "clean", "correctable", "quarantined", "superseded"

# Findings that make a sheet unsafe to copy structure or marks from.
BLOCKING = {"SUM-ROWS", "SUM-TOTAL-SPLIT", "SUM-NOT-100", "SUM-MISMATCH", "SUM-SPLIT", "SUM-NO-TOTAL",
            "TXT-PLACEHOLDER", "OPT-COUNT", "AT-MISSING", "AT-RANGE", "ID-NO-CLIENT"}
# Findings the generator corrects on reuse.
CORRECTED = {"AT-TEXT-SAYS-ATTACH", "AT-ASKS-TEXT", "UP-BOTH-FLAGS"}

# A value is the client's convention when enough of its sheets agree:
# 90% over 5+ uses, or 80% over 10+ (more history, less agreement needed).
CONVENTION_RULES = [(0.9, 5), (0.8, 10)]
CONTESTED_MIN = 3         # a disagreement needs this many uses to be worth a question


@dataclass
class Grade:
    tier: str
    reasons: List[str] = field(default_factory=list)


def year_of(sheet: Sheet) -> Optional[int]:
    m = re.search(r"(20\d\d)", sheet.code or sheet.title or "")
    return int(m.group(1)) if m else None


def grade(sheets: List[Sheet]) -> Dict[str, Grade]:
    out: Dict[str, Grade] = {}
    for s in sheets:
        rules = sorted({f.rule for f in validate_sheet(s)})
        block = [r for r in rules if r in BLOCKING]
        if block:
            out[s.name] = Grade(QUARANTINED, block)
        elif any(r in CORRECTED or r == "OPT-MAX-MARKS" for r in rules):
            out[s.name] = Grade(CORRECTABLE, [r for r in rules if r in CORRECTED or r == "OPT-MAX-MARKS"])
        else:
            out[s.name] = Grade(CLEAN)
    # Of two near-identical sheets, keep the one with a known client, then the
    # newer, then the one with fewer problems; the other is superseded.
    by_name = {s.name: s for s in sheets}
    for f in check_corpus(sheets):
        a = by_name[f.sheet]
        other = re.search(r"identical to (\S+) \(", f.message).group(1)
        b = by_name.get(other)
        if b is None:
            continue
        rank = lambda s: (s.client is not None, year_of(s) or 0, -len(validate_sheet(s)))
        loser = a if rank(a) < rank(b) else b
        winner = b if loser is a else a
        out[loser.name] = Grade(SUPERSEDED, [f"earlier copy of {winner.name}"])
    return out


def usable(sheets: List[Sheet], grades: Dict[str, Grade]) -> List[Sheet]:
    return [s for s in sheets if grades.get(s.name, Grade(CLEAN)).tier in (CLEAN, CORRECTABLE)]


# --- client conventions ---------------------------------------------------------

FOLLOW_UP = re.compile(r"^(if\b|please specify|specify\b|provide details|please provide details|"
                       r"please give details)", re.I)


def context_key(rows: List[Row], i: int) -> str:
    """Question key; generic follow-ups are keyed with the question they
    follow, since 'If yes, provide details' means something different under
    each parent."""
    k = normalise(rows[i].criteria)
    if i > 0 and FOLLOW_UP.match(rows[i].criteria.strip()):
        return normalise(rows[i - 1].criteria) + " >> " + k
    return k


def upload_value(r: Row) -> str:
    if r.attachment and r.compliance_attach:
        return "both"
    return "compliance" if r.compliance_attach else "attachment" if r.attachment else "none"


FIELDS = {
    "answer_type": lambda r: r.answer_type,
    "mandatory": lambda r: r.mandatory,
    "upload_flag": upload_value,
}


class Conventions:
    """Per client, per question, per field: what the client's sheets say."""

    def __init__(self, sheets: List[Sheet]):
        self.counts: Dict[Tuple[str, str, str], Counter] = defaultdict(Counter)
        for s in sheets:
            if not s.client:
                continue
            for i, r in enumerate(s.rows):
                k = context_key(s.rows, i)
                for f, get in FIELDS.items():
                    v = get(r)
                    if v is not None:
                        self.counts[(s.client, k, f)][v] += 1

    def convention(self, client: str, key: str, fld: str):
        """(value, agreeing, total) if the client has a clear convention, else None."""
        c = self.counts.get((client, key, fld))
        if not c:
            return None
        v, n = c.most_common(1)[0]
        total = sum(c.values())
        if any(total >= least and n / total >= share for share, least in CONVENTION_RULES):
            return v, n, total
        return None

    def contested(self, client: str, key: str, fld: str) -> Optional[Counter]:
        c = self.counts.get((client, key, fld))
        if not c or len(c) < 2 or sum(c.values()) < CONTESTED_MIN:
            return None
        return None if self.convention(client, key, fld) else c


# --- standing rulings -----------------------------------------------------------

RULING_COLUMNS = ["id", "client", "question_key", "field", "value", "decided_on", "question"]


def parse_value(fld: str, text: str):
    """A reviewer's answer as a field value, or raise ValueError."""
    t = str(text).strip().lower()
    if fld == "answer_type":
        if t in {"1", "2", "3", "4", "5"}:
            return int(t)
        raise ValueError("write an answer type 1-5")
    if fld == "mandatory":
        if t in ("true", "yes", "mandatory"):
            return True
        if t in ("false", "no", "optional"):
            return False
        raise ValueError("write 'true' or 'false'")
    if fld == "upload_flag":
        alias = {"l": "attachment", "m": "compliance"}
        t = alias.get(t, t)
        if t in ("none", "attachment", "compliance"):
            return t
        raise ValueError("write 'attachment' (L), 'compliance' (M) or 'none'")
    raise ValueError(f"unknown field {fld}")


def load_rulings(path: str):
    """{(client, key, field): value} and {ruling id: row} from a rulings CSV."""
    import csv
    import os
    rulings, by_id = {}, {}
    if not path or not os.path.exists(path):
        return rulings, by_id
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            v = parse_value(row["field"], row["value"])
            rulings[(row["client"], row["question_key"], row["field"])] = v
            by_id[row["id"]] = row
    return rulings, by_id


def save_rulings(path: str, new_rows: List[dict]):
    """Add or replace rulings; one row per (client, question, field)."""
    import csv
    import os
    _, by_id = load_rulings(path)
    for r in new_rows:
        by_id[r["id"]] = r
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=RULING_COLUMNS)
        w.writeheader()
        for r in sorted(by_id.values(), key=lambda r: (r["client"], r["question"], r["field"])):
            w.writerow({k: r.get(k, "") for k in RULING_COLUMNS})
