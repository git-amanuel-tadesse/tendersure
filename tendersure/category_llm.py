"""Model-assisted category extraction, checked by code.

The layout parser in `categories.py` is exact but brittle: it relies on the
ERCS notice's table geometry. Another client's notice - a different table, a
Word export, merged cells - can defeat it. A model reads the PDF directly and
returns the table as structured data, whatever the layout.

The model extracts; the code verifies. Sheet titles copy the description
verbatim, so every description and sub-category the model returns must be
found word-for-word in the PDF's own text layer. Anything that is not -
paraphrased, "tidied", invented - is reported, never silently accepted.

With both engines available, `reconcile` merges them per category number:
where they agree, the layout text is kept; where they disagree, the version
that passes the verbatim check wins and the disagreement is reported.

Only anonymised text leaves the machine: the notice's text layer is
extracted locally and passed through privacy.py; the PDF itself is never
sent. Works with any configured backend (see llm.py).
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import pdfplumber

from .categories import Category, category_kind, check_sequence
from .llm import make_llm
from .privacy import PrivacyError

KINDS = ["goods", "goods+service", "service", "consultancy", "works"]

SYSTEM = """You extract the procurement category table from a supplier prequalification notice.

Copy text exactly as printed. Join words that wrap onto a new line with a single space, but do not correct spelling, capitalisation, punctuation or grammar, and do not expand or shorten anything: the descriptions become official sheet titles.

For each numbered category give:
- number: the category number as printed
- description: the full category description
- group: the heading row the category sits under (for example GENERAL, ICT), exactly as printed; empty if the table has no group headings
- sub_categories: the items in the sub-category column, one entry per item, in order; empty if none
- kind: goods (supply of items only), goods+service (supply plus installation, maintenance or repair), service, consultancy (consultancy, advisory, audit, studies, research, certification), or works (construction and civil or electro-mechanical works)

Also report the notice's title, the client organisation's name, and the closing date for submissions as printed (empty if absent)."""

SCHEMA = {
    "type": "object",
    "properties": {
        "notice_title": {"type": "string"},
        "client_name": {"type": "string"},
        "closing_date": {"type": "string"},
        "categories": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "number": {"type": "integer"},
                "description": {"type": "string"},
                "group": {"type": "string"},
                "sub_categories": {"type": "array", "items": {"type": "string"}},
                "kind": {"type": "string", "enum": KINDS},
            },
            "required": ["number", "description", "group", "sub_categories", "kind"],
            "additionalProperties": False,
        }},
    },
    "required": ["notice_title", "client_name", "closing_date", "categories"],
    "additionalProperties": False,
}


@dataclass
class Notice:
    title: str = ""
    client_name: str = ""
    closing_date: str = ""


@dataclass
class Issue:
    number: Optional[int]
    kind: str          # unverified | sequence | disagreement | kind | no-text-layer
    message: str


@dataclass
class Extraction:
    categories: List[Category]
    notice: Notice = field(default_factory=Notice)
    issues: List[Issue] = field(default_factory=list)
    model_kinds: Dict[int, str] = field(default_factory=dict)


# --- verification -------------------------------------------------------------

def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def pdf_tokens(pdf_path: str) -> Set[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return {t for page in pdf.pages for t in _tokens(page.extract_text() or "")}


def _missing(text: str, vocab: Set[str]) -> List[str]:
    return [t for t in _tokens(text) if t not in vocab]


def verify(cats: List[Category], vocab: Set[str]) -> List[Issue]:
    """Every word of every description and sub-category must occur in the PDF."""
    if not vocab:
        return [Issue(None, "no-text-layer",
                      "the PDF has no text layer (scanned?); descriptions cannot be checked "
                      "against it and must be read by a person")]
    issues = [Issue(None, "sequence", p) for p in check_sequence(cats)]
    for c in cats:
        miss = _missing(c.description, vocab)
        if miss:
            issues.append(Issue(c.number, "unverified",
                                f"description has words not in the PDF: {', '.join(sorted(set(miss)))}"))
        for s in c.sub_categories:
            miss = _missing(s, vocab)
            if miss:
                issues.append(Issue(c.number, "unverified",
                                    f"sub-category '{s}' has words not in the PDF: {', '.join(sorted(set(miss)))}"))
    return issues


# --- model extraction ----------------------------------------------------------

def notice_text(pdf_path: str) -> str:
    """The notice as text, columns kept in place, for anonymising and sending.

    The raw PDF is never sent: it cannot be scrubbed reliably. A scanned
    notice has no text layer and cannot be anonymised, so it is refused.
    """
    with pdfplumber.open(pdf_path) as pdf:
        pages = [(p.extract_text(layout=True) or "").rstrip() for p in pdf.pages]
    if not any(t.strip() for t in pages):
        raise PrivacyError("the notice has no text layer (scanned?), so it cannot be anonymised; "
                           "it was not sent. Use --engine layout, or OCR it locally first")
    return table_only("\n\n".join(t for t in pages if t.strip()))


_PROSE_WORDS = 14


def table_only(text: str) -> str:
    """Keep the category table, drop the narrative.

    Replacing names is not enough for prose: "one of the 192 national Red
    Cross societies, founded in 1935" identifies the client by itself. The
    extractor needs only the table, so only the table is sent: everything
    from its header line on, minus any line that reads like a sentence.
    """
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if re.search(r"\bCATEGOR(Y|IES)\b", l, re.I) and len(l.split()) <= 8), 0)
    kept = []
    for l in lines[start:]:
        words = l.split()
        if len(words) >= _PROSE_WORDS and re.search(r"[.;:]\s*$|\b(the|and|of|is|are|will|which)\b.*\b(the|and|of)\b", l, re.I) \
                and not re.match(r"\s*\d{1,3}\s", l):
            continue                                  # a sentence, not a table row
        kept.append(l.rstrip())
    return "\n".join(kept)


class LLMExtractor:
    """Any configured model (see llm.py). Pass `llm` to inject a client."""

    def __init__(self, llm=None, model: Optional[str] = None):
        self.llm = llm or make_llm(model=model)

    def ask(self, pdf_path: str, client: Optional[str] = None) -> dict:
        text = notice_text(pdf_path)
        return self.llm.complete_json(
            SYSTEM, "Extract the category table from this notice. Its text follows, with the table "
                    "columns kept in place; a category number may sit on a middle line of its "
                    "description.\n\n" + text,
            SCHEMA, "category_table", client=client, max_tokens=24000)

    def extract(self, pdf_path: str, client: Optional[str] = None,
                version: Optional[str] = None) -> Extraction:
        data = self.ask(pdf_path, client)
        notice = Notice(data.get("notice_title", ""), data.get("client_name", ""),
                        data.get("closing_date", ""))
        version = version or (f"closing {notice.closing_date}" if notice.closing_date else None)
        cats, kinds = [], {}
        for d in data.get("categories", []):
            cats.append(Category(
                number=int(d["number"]),
                description=" ".join(d["description"].split()),
                group=" ".join(d["group"].split()) or None,
                sub_categories=[" ".join(s.split()) for s in d["sub_categories"] if s.strip()],
                client=client, version=version,
            ))
            kinds[int(d["number"])] = d["kind"]
        cats.sort(key=lambda c: c.number)
        ex = Extraction(cats, notice, model_kinds=kinds)
        ex.issues = verify(cats, pdf_tokens(pdf_path)) + _kind_issues(cats, kinds)
        return ex


def _kind_issues(cats: List[Category], kinds: Dict[int, str]) -> List[Issue]:
    """Where the model and the keyword heuristic disagree on procurement kind.

    Retrieval still ranks with the heuristic (past sheets have no model label),
    so a disagreement is a hint to fix the heuristic, not an override.
    """
    out = []
    for c in cats:
        k = kinds.get(c.number)
        if k and k != category_kind(c.description):
            out.append(Issue(c.number, "kind",
                             f"model says {k}, heuristic says {category_kind(c.description)}"))
    return out


# --- reconciliation ------------------------------------------------------------

def _norm(text: Optional[str]) -> str:
    return " ".join(_tokens(text or ""))


def reconcile(layout: List[Category], model: List[Category],
              vocab: Set[str]) -> Tuple[List[Category], List[Issue]]:
    """Merge two extractions of the same notice, per category number."""
    a = {c.number: c for c in layout}
    b = {c.number: c for c in model}
    merged, issues = [], []
    for n in sorted(set(a) | set(b)):
        la, mo = a.get(n), b.get(n)
        if la is None or mo is None:
            only = la or mo
            src = "layout parser" if la else "model"
            issues.append(Issue(n, "disagreement", f"found only by the {src}: {only.description}"))
            merged.append(only)
            continue
        pick = la
        for f in ("description", "group"):
            va, vb = getattr(la, f), getattr(mo, f)
            if _norm(va) == _norm(vb):
                continue
            a_ok = not _missing(va or "", vocab)
            b_ok = not _missing(vb or "", vocab)
            winner = "layout" if a_ok or not b_ok else "model"
            if winner == "model":
                pick = _with(pick, **{f: vb})
            issues.append(Issue(n, "disagreement",
                                f"{f} differs; kept {winner}\n    layout: {va}\n    model:  {vb}"))
        if [_norm(s) for s in la.sub_categories] != [_norm(s) for s in mo.sub_categories]:
            a_ok = all(not _missing(s, vocab) for s in la.sub_categories)
            use_model = not a_ok and all(not _missing(s, vocab) for s in mo.sub_categories)
            if use_model:
                pick = _with(pick, sub_categories=list(mo.sub_categories))
            issues.append(Issue(n, "disagreement",
                                f"sub-categories differ; kept {'model' if use_model else 'layout'}\n"
                                f"    layout: {la.sub_categories}\n    model:  {mo.sub_categories}"))
        merged.append(pick)
    issues += [Issue(None, "sequence", p) for p in check_sequence(merged)]
    return merged, issues


def _with(c: Category, **changes) -> Category:
    d = dict(c.__dict__)
    d.update(changes)
    return Category(**d)
