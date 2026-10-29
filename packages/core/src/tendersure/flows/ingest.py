"""Ingest: a folder of workbooks into the bank, and the bank's reports."""

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import trust
from ..bank import Bank
from ..reports import write_bank, write_validation_report
from ..validate import check_corpus, validate_sheet
from .common import FlowError, log


@dataclass
class IngestResult:
    added: int                  # workbooks new to the bank
    sheets_added: int
    unchanged: int
    replaced: int
    sheets: int                 # in the bank after the ingest
    questions: int
    findings: Dict[str, int]    # by kind: platform, silent
    tiers: Dict[str, int]       # sheet grades: clean, correctable, quarantined, superseded
    bank_questions: int
    conflicts: int
    reports: Dict[str, str] = field(default_factory=dict)   # validation, bank -> path


def ingest(corpus: str, bank: str, out: str, label: Optional[str] = None) -> IngestResult:
    """`label`: `corpus` is a staging copy (an upload); see BankStore.ingest_folder."""
    from ..store import BankStore
    if not os.path.isdir(corpus):
        raise FlowError(f"folder not found: {corpus}\n  quote the path if it contains spaces")
    store = BankStore(bank)
    st = store.ingest_folder(corpus, label)
    log.info(f"bank {bank}: {st['added']} workbook(s) added ({st['sheets_added']} sheets), "
             f"{st['unchanged']} already in the bank, {st['replaced']} replaced by a newer version")
    sheets = store.load_sheets()
    findings = [f for s in sheets for f in validate_sheet(s)] + check_corpus(sheets)
    b = Bank()
    b.ingest(sheets)
    b.finalise()
    os.makedirs(out, exist_ok=True)
    grades = trust.grade(sheets)
    vr = write_validation_report(sheets, findings, os.path.join(out, "validation_report.xlsx"), grades)
    br = write_bank(b, os.path.join(out, "bank.xlsx"))
    t = b.tiers()
    kinds = Counter(f.kind for f in findings)
    questions = sum(len(s.rows) for s in sheets)
    log.info(f"{len(sheets)} sheets, {questions} questions")
    log.info(f"{len(findings)} findings ({kinds['platform']} platform, {kinds['silent']} silent)")
    tiers = Counter(g.tier for g in grades.values())
    log.info(f"sheets graded automatically: {tiers['clean']} clean, {tiers['correctable']} fixed on reuse, "
             f"{tiers['quarantined']} quarantined (never copied), {tiers['superseded']} superseded - no review needed")
    conflicts = len(b.conflicts())
    log.info(f"bank: {len(b.entries)} questions (core {len(t['core'])}, common {len(t['common'])}, "
             f"category {len(t['category'])}); {conflicts} cross-sheet disagreements (reference only: "
             f"settled by client convention, or asked in a batch that uses them)")
    log.info(f"wrote {vr}\nwrote {br}")
    return IngestResult(st["added"], st["sheets_added"], st["unchanged"], st["replaced"], len(sheets), questions,
                        dict(kinds), dict(tiers), len(b.entries), conflicts, {"validation": vr, "bank": br})


@dataclass
class BankStatus:
    path: str
    exists: bool
    workbooks: int = 0
    sheets: int = 0
    questions: int = 0
    clients: List[str] = field(default_factory=list)
    last_ingest: Optional[str] = None


def bank_status(bank: Optional[str] = None) -> BankStatus:
    """What the bank holds, without loading it."""
    from ..paths import current
    from ..store import BankStore
    bank = bank or current().bank
    if not os.path.exists(bank):
        return BankStatus(bank, False)
    store = BankStore(bank)
    s = store.summary()
    return BankStatus(bank, True, s["files"], s["sheets"], s["questions"], store.clients(), store.last_ingest())
