"""Research (spec 05): technical names, questions, licences and document
names - our own data first, then the web.

The team spends time searching the web for what a category is properly
called, what its suppliers should be asked, which licence or standard
applies in the country, and what a document is officially called. This
module answers from the bank first - past categories, questions and
documents of every client - and sends only what the bank cannot answer to a
web search (Gemini with Google Search). Every finding carries its sources;
none is applied without the team. Findings are kept by topic and country,
not by client, so one client's research serves all; a confirmed finding is
never researched again. Nothing here is tuned to a client: prompts carry a
topic and a country only.
"""

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .learned_store import resolve
from .llm import LLMError, make_llm
from .parser import Sheet, normalise, section_key
from .similarity import TfidfEmbedder, cosine
from .validate import needs_names

log = logging.getLogger("tendersure")

RESEARCH = "research"
KINDS = ("names", "licences", "questions", "documents")
BANK, WEB = "bank", "web"
DRAFTED, CONFIRMED = "drafted", "confirmed"
COLUMNS = ["kind", "topic_key", "country", "topic", "proposal", "sources", "origin", "status"]
NAME_MATCH = 0.6           # a past category this close names the new one
LICENCE_MATCH = 0.45       # past categories this close lend their licences
_LICENCE = re.compile(r"licen[cs]e|certif|registration|permit|authori[sz]|accredit|\bISO\b|member", re.I)
_COUNTRY_WORDS = {"ET": ("Ethiopia", "Ethiopian"), "KE": ("Kenya", "Kenyan", "KES"), "UG": ("Uganda", "Ugandan"),
                  "TZ": ("Tanzania", "Tanzanian"), "RW": ("Rwanda", "Rwandan"), "SO": ("Somalia", "Somali"),
                  "SS": ("South Sudan",)}
_LICENCE_SECTIONS = {"technical", "hr", "experience", "sla"}     # not statutory registration documents
_PEOPLE = re.compile(r"\b(cv|resumes?|curriculum|staff|workforce|personnel|technicians?|engineers?)\b", re.I)
_VERBS = re.compile(r"\b(supply|supplies|provision|provide|installation|install|maintenance|maintain|repairs?|"
                    r"delivery|servicing|services?|and|of|the|for|etc|related|general)\b", re.I)


def topic_key(text: str) -> str:
    return normalise(text)


@dataclass
class Finding:
    kind: str
    topic_key: str
    country: str
    topic: str
    proposal: dict
    sources: List[str]
    origin: str
    status: str = DRAFTED


class Table:
    def __init__(self, findings: List[Finding]):
        self.findings = findings
        self._by = {(f.kind, f.topic_key, f.country): f for f in findings}

    def lookup(self, kind: str, topic: str, country: str) -> Optional[Finding]:
        return self._by.get((kind, topic_key(topic), country or ""))

    def add(self, f: Finding):
        old = self._by.get((f.kind, f.topic_key, f.country))
        if old is not None:
            if old.status == CONFIRMED:
                return                                  # the team's word stands
            self.findings.remove(old)
        self.findings.append(f)
        self._by[(f.kind, f.topic_key, f.country)] = f


def load(target) -> Table:
    store, name = resolve(target, RESEARCH)
    return Table([Finding(d["kind"], d["topic_key"], d["country"], d["topic"], json.loads(d["proposal"] or "{}"),
                          json.loads(d["sources"] or "[]"), d["origin"], d["status"] or DRAFTED)
                  for d in store.read(name)])


def save(table: Table, target):
    store, name = resolve(target, RESEARCH)
    store.write(name, COLUMNS, [{"kind": f.kind, "topic_key": f.topic_key, "country": f.country, "topic": f.topic,
                                 "proposal": json.dumps(f.proposal, ensure_ascii=False),
                                 "sources": json.dumps(f.sources, ensure_ascii=False),
                                 "origin": f.origin, "status": f.status}
                                for f in sorted(table.findings, key=lambda f: (f.kind, f.country, f.topic_key))])


# --- our own data first ------------------------------------------------------------

def sheet_country(s: Sheet) -> str:
    """The country a past sheet is for: its code's (ERCS-ET-2026-002 -> ET),
    else the country its questions name most (a Kenyan sheet names KES,
    the Law Society of Kenya...); '' if neither says."""
    m = re.search(r"[-_]([A-Z]{2})[-_]", s.code or "")
    if m and m.group(1) in _COUNTRY_WORDS:
        return m.group(1)
    text = " ".join(r.criteria for r in s.rows)
    counts = {c: sum(len(re.findall(rf"\b{w}\b", text)) for w in words) for c, words in _COUNTRY_WORDS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] else ""


def _subject(text: str) -> str:
    return _VERBS.sub(" ", re.sub(r"\([^)]*\)?", " ", text or ""))


class BankKnowledge:
    """What past exercises of every client say about a topic. Sources name a
    past exercise's category, never its client."""

    def __init__(self, sheets: List[Sheet]):
        seen: Dict[str, Sheet] = {}
        for s in sheets:
            if s.description:
                seen.setdefault(" ".join(s.description.lower().split()), s)
        self.sheets = list(seen.values())
        self.country = {s.name: sheet_country(s) for s in self.sheets}
        self.embedder = TfidfEmbedder().fit([_subject(s.description) for s in self.sheets] or [""])
        self.vectors = [self.embedder.vector(_subject(s.description)) for s in self.sheets]

    def similar(self, text: str, at_least: float) -> List[Tuple[float, Sheet]]:
        v = self.embedder.vector(_subject(text))
        scored = sorted(((cosine(v, w), s) for w, s in zip(self.vectors, self.sheets)), key=lambda x: -x[0])
        return [(score, s) for score, s in scored if score >= at_least]

    def name(self, topic: str, country: str) -> Optional[Finding]:
        hits = self.similar(topic, NAME_MATCH)
        if not hits:
            return None
        score, s = hits[0]
        return Finding("names", topic_key(topic), country, topic,
                       {"technical_name": s.description.strip(), "includes": [], "usually_split_as": []},
                       [f"a past exercise's category ({score:.0%} alike): {s.description.strip()}"], BANK)

    def licences(self, topic: str, country: str) -> Optional[Finding]:
        """Category-specific licences past sheets of the same country ask for
        on similar categories - licences are national, so another country's
        are no evidence; statutory registration documents are not counted."""
        if not country:
            return None
        hits = [(sc, s) for sc, s in self.similar(topic, LICENCE_MATCH) if self.country[s.name] == country][:5]
        asked = Counter()
        for _, s in hits:
            asked.update({r.archive_short_name or r.criteria.strip() for r in s.rows
                          if needs_names(r) and _LICENCE.search(r.criteria) and not _PEOPLE.search(r.criteria)
                          and section_key(r.section) in _LICENCE_SECTIONS})
        if not asked:
            return None
        return Finding("licences", topic_key(topic), country, topic,
                       {"licences": [{"name": n, "issuer": "", "required": False} for n, _ in asked.most_common(5)]},
                       [f"asked in {n} of {len(hits)} past questionnaires on similar categories: {d}"
                        for d, n in asked.most_common(5)], BANK)


# --- then the web ----------------------------------------------------------------------

SYSTEM = (
    "You research procurement categories for a supplier prequalification questionnaire, the way an "
    "experienced procurement officer would, using web search. Answer only from what you find; for a "
    "country, use that country's regulators and laws. Keep names short and standard."
)
_SCHEMAS = {
    "names": {"type": "object", "required": ["technical_name"], "properties": {
        "technical_name": {"type": "string"}, "includes": {"type": "array", "items": {"type": "string"}},
        "usually_split_as": {"type": "array", "items": {"type": "string"}}}},
    "licences": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string"}, "issuer": {"type": "string"}, "required": {"type": "boolean"}}}},
    "questions": {"type": "array", "items": {"type": "object", "required": ["question", "answer_type"], "properties": {
        "question": {"type": "string"}, "answer_type": {"type": "integer", "enum": [1, 2, 3, 4, 5]}}}},
    "documents": {"type": "array", "items": {"type": "object", "required": ["abbreviation", "official_name"],
                                             "properties": {"abbreviation": {"type": "string"},
                                                            "official_name": {"type": "string"},
                                                            "short_name": {"type": "string"}}}},
}
_ASK = {
    "names": "names: the standard technical or trade name procurement teams use for this category; what it "
             "normally includes (includes); and how such a category is usually split between kinds of "
             "specialist supplier (usually_split_as), if it usually is",
    "licences": "licences: the licences, registrations or certifications a supplier of this in the country must "
                "or usually holds, with the issuing body (issuer) and whether the law requires it (required)",
    "questions": "questions: up to 8 technical questions a buyer should ask a supplier of this category, each "
                 "with an answer type (1 text, 2 single choice, 3 multiple choice, 4 yes/no, 5 document upload)",
    "documents": "documents: for each abbreviation listed, the document's official name in the country and a "
                 "short name of at most four words",
}


def build_prompt(topic: str, country: str, kinds: List[str]) -> str:
    """The whole request: a topic, a country and what to find - nothing about a client."""
    where = f"Country: {country}." if country else "Country: not given (answer generally)."
    return "\n".join([f"Topic: {topic}", where, "", "Find:"] + [f"- {_ASK[k]}" for k in kinds])


class WebResearcher:
    """Gemini with Google Search. A finding the search gave no source for is dropped."""

    def __init__(self, llm=None, model: Optional[str] = None):
        self.llm = llm or make_llm(model=model)

    def research(self, topic: str, country: str, kinds: List[str], client: Optional[str] = None) -> List[Finding]:
        schema = {"type": "object", "properties": {k: _SCHEMAS[k] for k in kinds}}
        try:
            raw = self.llm.complete_json(SYSTEM, build_prompt(topic, country, kinds), schema, "research",
                                         client=client, max_tokens=6000, search=True)
        except LLMError as e:
            log.info(f"research: '{topic[:50]}' not researched on the web ({e})")
            return []
        sources = [f"{s['title']} | {s['uri']}" for s in self.llm.last_sources]
        if not sources:
            return []
        out = []
        for k in kinds:
            value = raw.get(k)
            if value:
                proposal = value if k == "names" else {k: value}
                out.append(Finding(k, topic_key(topic), country, topic, proposal, sources, WEB))
        return out


def research(topics: List[str], country: str, kinds: List[str], table: Table, bank: Optional[BankKnowledge],
             web: Optional[WebResearcher] = None, client: Optional[str] = None) -> List[Finding]:
    """Findings for every topic and kind the table lacks: the bank first, the
    web for the rest (one call per topic). Returns the new findings."""
    new = []
    for topic in dict.fromkeys(topics):
        todo = [k for k in kinds if table.lookup(k, topic, country) is None]
        for k in list(todo):
            f = bank.name(topic, country) if bank and k == "names" else \
                bank.licences(topic, country) if bank and k == "licences" else None
            if f:
                table.add(f)
                new.append(f)
                todo.remove(k)
        if todo and web is not None:
            for f in web.research(topic, country, todo, client):
                table.add(f)
                new.append(f)
    return new


def describe(f: Finding) -> str:
    """A finding's proposal as one line of text."""
    p = f.proposal
    if f.kind == "names":
        parts = [p.get("technical_name", "")]
        if p.get("includes"):
            parts.append("includes: " + ", ".join(p["includes"]))
        if p.get("usually_split_as"):
            parts.append("usually split as: " + " | ".join(p["usually_split_as"]))
        return "; ".join(parts)
    if f.kind == "licences":
        return "; ".join(f"{x['name']}" + (f" ({x['issuer']})" if x.get("issuer") else "") +
                         (" - required by law" if x.get("required") else "") for x in p.get("licences", []))
    if f.kind == "questions":
        return " | ".join(f"{q['question']} [type {q['answer_type']}]" for q in p.get("questions", []))
    return "; ".join(f"{d['abbreviation']}: {d['official_name']}" for d in p.get("documents", []))


# --- the team's review --------------------------------------------------------------

REVIEW_HEADERS = ["ID", "Kind", "Topic", "Country", "Proposal", "Sources", "From", "Decision", "Key"]


def write_review(findings: List[Finding], path: str) -> int:
    """Every finding still a draft, for the team: answer ok, or the corrected
    proposal (names: the name; licences: names separated by ';'; questions:
    separated by '|'; documents: 'ABBR: official name' separated by ';')."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Research"
    ws.append(REVIEW_HEADERS)
    drafts = [f for f in findings if f.status != CONFIRMED]
    for i, f in enumerate(drafts, 1):
        ws.append([f"R-{i}", f.kind, f.topic, f.country, describe(f), "\n".join(f.sources), f.origin, "",
                   f"{f.kind}\t{f.topic_key}\t{f.country}"])
    for c, w in zip(ws[1], [7, 10, 45, 8, 70, 60, 7, 30, 10]):
        c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="FFC5D9F1")
        ws.column_dimensions[c.column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
        row[7].fill = PatternFill("solid", fgColor="FFFFF2CC")
    ws.column_dimensions["I"].hidden = True
    ws.freeze_panes = "C2"
    wb.save(path)
    return len(drafts)


def _corrected(kind: str, text: str) -> dict:
    if kind == "names":
        return {"technical_name": text, "includes": [], "usually_split_as": []}
    if kind == "licences":
        return {"licences": [{"name": x.strip(), "issuer": "", "required": False} for x in text.split(";") if x.strip()]}
    if kind == "questions":
        return {"questions": [{"question": x.strip(), "answer_type": 1} for x in text.split("|") if x.strip()]}
    pairs = [x.split(":", 1) for x in text.split(";") if ":" in x]
    return {"documents": [{"abbreviation": a.strip(), "official_name": b.strip(), "short_name": ""} for a, b in pairs]}


def apply_review(path: str, table: Table) -> Tuple[int, List[str]]:
    import openpyxl
    ws = openpyxl.load_workbook(path)["Research"]
    col = {c.value: i for i, c in enumerate(ws[1])}
    applied, problems = 0, []
    for row in ws.iter_rows(min_row=2, values_only=True):
        answer = str(row[col["Decision"]] or "").strip()
        if not answer:
            continue
        kind, key, country = (str(row[col["Key"]]).split("\t") + ["", "", ""])[:3]
        f = table._by.get((kind, key, country))
        if f is None:
            problems.append(f"{row[col['ID']]}: no such finding any more")
            continue
        if answer.lower() != "ok":
            f.proposal = _corrected(kind, answer)
            f.sources = f.sources + ["corrected by the team"]
        f.status = CONFIRMED
        applied += 1
    return applied, problems


def country_of(prefix: Optional[str]) -> str:
    """'ERCS-ET-2026-' -> 'ET'; a prefix without a country code -> ''."""
    m = re.search(r"[-_]([A-Z]{2})[-_]", prefix or "")
    return m.group(1) if m else ""


def document_names(table: Table, country: str) -> Dict[str, Tuple[str, str]]:
    """Abbreviation -> (official name, first source), from every documents finding for the country."""
    out: Dict[str, Tuple[str, str]] = {}
    for f in table.findings:
        if f.kind == "documents" and f.country == country:
            for d in f.proposal.get("documents", []):
                if d.get("abbreviation") and d.get("official_name"):
                    out.setdefault(d["abbreviation"].strip().upper(), (d["official_name"], f.sources[0] if f.sources else ""))
    return out


def research_documents(abbreviations: List[str], country: str, table: Table, web: Optional[WebResearcher],
                       client: Optional[str] = None) -> List[Finding]:
    """Official names for abbreviations the table does not know yet, in one search."""
    todo = [a for a in abbreviations if a not in document_names(table, country)]
    if not todo or web is None:
        return []
    found = web.research("Documents suppliers are asked for, abbreviated as: " + ", ".join(todo), country,
                         ["documents"], client)
    for f in found:
        table.add(f)
    return found


def batch_research(plans, country: str, table: Table, bank: Optional[BankKnowledge], web: Optional[WebResearcher],
                   client: Optional[str], known: set) -> List[Finding]:
    """What generate needs researched for a batch: questions and licences for
    categories no past sheet covers, and unknown document abbreviations."""
    from .generate import unknown_abbreviations
    weak = [p.target.description for p in plans if p.weak_match]
    new = research(weak, country, ["questions", "licences"], table, bank, web, client)
    return new + research_documents(unknown_abbreviations(plans, known), country, table, web, client)


def read_target(path: str) -> Optional[Tuple[int, int]]:
    """The target a revised list was proposed against, from its Summary tab."""
    import openpyxl
    try:
        ws = openpyxl.load_workbook(path, read_only=True)["Summary"]
    except (KeyError, FileNotFoundError):
        return None
    for row in ws.iter_rows(values_only=True):
        if row and row[0] == "Target" and row[1]:
            a, _, b = str(row[1]).partition("-")
            return (int(a), int(b)) if a.isdigit() and b.isdigit() else None
    return None
