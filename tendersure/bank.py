"""Build the question bank from parsed criteria sheets.

A bank entry is one question concept. It records every wording variant seen,
which clients and categories used it, and the settled value of each structural
field. Where the corpus disagrees on a structural field, the entry carries a
CONFLICT for a human to rule on - the bank never silently picks a winner on
anything that matters.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from .parser import Sheet, Row, normalise
from .schema import ANSWER_TYPES


@dataclass
class Conflict:
    field_name: str
    values: Dict[str, int]
    majority: str
    minority_locations: List[str]

    @property
    def confidence(self) -> float:
        total = sum(self.values.values())
        return max(self.values.values()) / total if total else 0.0


@dataclass
class BankEntry:
    key: str
    canonical_text: str
    variants: Counter = field(default_factory=Counter)
    sheets: List[str] = field(default_factory=list)
    clients: Counter = field(default_factory=Counter)
    sections: Counter = field(default_factory=Counter)
    sub_sections: Counter = field(default_factory=Counter)
    answer_types: Counter = field(default_factory=Counter)
    mandatory: Counter = field(default_factory=Counter)
    upload_flag: Counter = field(default_factory=Counter)
    options: Counter = field(default_factory=Counter)
    archive_names: Counter = field(default_factory=Counter)
    acronyms: Counter = field(default_factory=Counter)
    marks_seen: List[float] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    _rows: List[Row] = field(default_factory=list, repr=False)

    @property
    def frequency(self) -> int:
        return len(set(self.sheets))

    def modal(self, counter: Counter):
        return counter.most_common(1)[0][0] if counter else None

    @property
    def settled_answer_type(self) -> Optional[int]:
        return self.modal(self.answer_types)

    @property
    def tier(self) -> str:
        return self._tier

    def compute_conflicts(self, n_sheets: int, min_freq: int = 3):
        """Flag structural fields where the corpus disagrees.

        Marks are deliberately excluded: they legitimately vary by client and
        category, so a majority there would be meaningless.
        """
        self.conflicts = []
        if self.frequency < min_freq:
            return
        for name, counter in (
            ("answer_type", self.answer_types),
            ("mandatory", self.mandatory),
            ("upload_flag", self.upload_flag),
        ):
            if len(counter) < 2:
                continue
            total = sum(counter.values())
            top, top_n = counter.most_common(1)[0]
            if top_n == total:
                continue
            minority = {str(k): v for k, v in counter.items() if k != top}
            locs = []
            for r in self._rows:
                val = {
                    "answer_type": r.answer_type,
                    "mandatory": r.mandatory,
                    "upload_flag": _upload_flag(r),
                }[name]
                if val != top:
                    locs.append(f"{r.sheet}!r{r.excel_row}")
            self.conflicts.append(
                Conflict(
                    field_name=name,
                    values={str(k): v for k, v in counter.items()},
                    majority=str(top),
                    minority_locations=locs,
                )
            )


def _upload_flag(r: Row) -> str:
    if r.attachment and r.compliance_attach:
        return "both"
    if r.compliance_attach:
        return "compliance"
    if r.attachment:
        return "attachment"
    return "none"


class Bank:
    def __init__(self):
        self.entries: Dict[str, BankEntry] = {}
        self.n_sheets = 0
        self.sheet_index: Dict[str, Sheet] = {}

    def ingest(self, sheets: List[Sheet]):
        for s in sheets:
            self.n_sheets += 1
            self.sheet_index[s.name] = s
            for r in s.rows:
                key = normalise(r.criteria)
                if len(key) < 4:
                    continue
                e = self.entries.get(key)
                if e is None:
                    e = BankEntry(key=key, canonical_text=r.criteria)
                    self.entries[key] = e
                e.variants[r.criteria] += 1
                e.sheets.append(s.name)
                if s.client:
                    e.clients[s.client] += 1
                if r.section:
                    e.sections[r.section.split("-")[0].strip()] += 1
                if r.sub_section:
                    e.sub_sections[r.sub_section] += 1
                if r.answer_type is not None:
                    e.answer_types[r.answer_type] += 1
                e.mandatory[r.mandatory] += 1
                e.upload_flag[_upload_flag(r)] += 1
                if r.options:
                    e.options[r.options] += 1
                if r.archive_short_name:
                    e.archive_names[r.archive_short_name] += 1
                if r.acronym:
                    e.acronyms[r.acronym] += 1
                if r.marks is not None:
                    e.marks_seen.append(r.marks)
                e._rows.append(r)

    def finalise(self):
        for e in self.entries.values():
            e.canonical_text = e.variants.most_common(1)[0][0]
            share = e.frequency / self.n_sheets if self.n_sheets else 0
            e._tier = ("core" if share >= 0.95
                       else "common" if share >= 0.50
                       else "category")
            e.compute_conflicts(self.n_sheets)

    # --- reporting helpers -------------------------------------------------

    def tiers(self) -> Dict[str, List[BankEntry]]:
        out = defaultdict(list)
        for e in self.entries.values():
            out[e.tier].append(e)
        for v in out.values():
            v.sort(key=lambda e: -e.frequency)
        return out

    def conflicts(self) -> List[BankEntry]:
        return sorted(
            [e for e in self.entries.values() if e.conflicts],
            key=lambda e: -e.frequency,
        )

    def answer_type_evidence(self) -> Dict[int, List[str]]:
        """Real example questions per answer type, drawn from the corpus."""
        out = defaultdict(list)
        for e in sorted(self.entries.values(), key=lambda e: -e.frequency):
            t = e.settled_answer_type
            if t in ANSWER_TYPES and len(out[t]) < 5 and not e.conflicts:
                out[t].append(e.canonical_text)
        return out
