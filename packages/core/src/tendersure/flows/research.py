"""Research (spec 05): technical names, licences, questions and document
names for a list of categories - our own data first, then the web."""

import os
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from .. import categories as cat
from .. import refine as rf
from .. import research as rs
from ..paths import current
from ..privacy import register_clients
from .common import FlowError, Model, load_source, log, research_tools


@dataclass
class ResearchResult:
    topics: List[str]
    country: str
    new: list                               # research.Finding found by this run
    review: Optional[str] = None            # the review workbook
    to_review: int = 0
    revised: Optional[str] = None           # a revised list whose research columns were filled
    applied: int = 0                        # answered lines applied (--review)
    problems: List[str] = field(default_factory=list)


def research(source_list: str, client: str, kinds: List[str], prefix: Optional[str] = None,
             country: Optional[str] = None, sheet: Optional[str] = None, out: Optional[str] = None,
             bank: Optional[str] = None, table_path: Optional[str] = None,
             model: Model = Model()) -> ResearchResult:
    """`source_list`: a draft list, a categories table, or a revised list (whose research columns are
    filled in place; its proposals stay as they are)."""
    bank = bank or current().bank
    table_path = table_path or current().learned(rs.RESEARCH)
    register_clients([client])
    if not source_list:
        raise FlowError("give a draft list or categories table, or --review <answered research review>")
    revised = None
    if source_list.endswith(".xlsx"):
        import openpyxl
        if {"Draft", "Revised"} <= set(openpyxl.load_workbook(source_list, read_only=True).sheetnames):
            revised = rf.read_revised(source_list)
    topics = [ln.description for ln in revised[1] if ln.status != rf.REPEATED] if revised else \
        [c.description for c in cat.load(source_list)] if source_list.endswith(".csv") \
        else [d.description for d in rf.read_draft(source_list, sheet)]
    country = country or rs.country_of(prefix)
    sheets = load_source(None, bank)[0] if os.path.exists(bank) else []
    table, kb, web = research_tools(table_path, sheets, model)
    new = rs.research(topics, country, kinds, table, kb, web, client)
    rs.save(table, table_path)
    r = ResearchResult(topics, country, new)
    if revised:
        rf.attach_research(revised[1], table, country)
        rf.write_revised(source_list, revised[0], revised[1], client, rs.read_target(source_list))
        r.revised = source_list
        log.info(f"research: research columns filled in {source_list} (its proposals unchanged)")
    r.review = out or os.path.join(current().reports, "research_review.xlsx")
    os.makedirs(os.path.dirname(os.path.abspath(r.review)), exist_ok=True)
    r.to_review = rs.write_review([f for f in table.findings if f.topic_key in {rs.topic_key(t) for t in topics}],
                                  r.review)
    by = Counter((f.kind, f.origin) for f in new)
    log.info(f"research: {len(topics)} topics, country {country or '-'}: {len(new)} new findings ("
             + ", ".join(f"{k} from the {o} {n_}" for (k, o), n_ in sorted(by.items()))
             + f"); {r.to_review} to review -> {r.review}")
    return r


def apply_review(review: str, client: str, table_path: Optional[str] = None) -> ResearchResult:
    """The answered research review: confirmed findings are never researched again."""
    table_path = table_path or current().learned(rs.RESEARCH)
    register_clients([client])
    table = rs.load(table_path)
    applied, problems = rs.apply_review(review, table)
    rs.save(table, table_path)
    log.info(f"research: applied {applied} answered lines from {review}")
    for p in problems:
        log.info(f"REVIEW PROBLEM: {p}")
    return ResearchResult([], "", [], applied=applied, problems=problems)
