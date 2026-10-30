"""Extract the category table from a client's prequalification notice (PDF).

The number in a criteria sheet's code IS the category number in the notice
(ERCS-ET-2026-019 = category 19), and the notice groups categories (GENERAL,
ICT, ...). That grouping is the client's own classification and a better
retrieval signal than text similarity, so it is captured here.

Notices get revised - deadlines move, descriptions grow - so every extracted
table carries a version label, and `diff` reports what changed between two.

Layout assumptions, taken from the ERCS 2026-2029 notice: a three-column table
(#, CATEGORY DESCRIPTION, SUB-CATEGORY) whose rows are separated by thin
border rectangles in the '#' column. Group headings are rows with no number.
"""

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict

import pdfplumber

# Column boundaries are read from the header words on each page; these are
# only fallbacks.
_DESC_X = 111.5
_SUB_X = 382.5
_SEP_MERGE = 4        # border rects within this many points are one rule
_WRAP_GAP = 14.5      # line gap above which a sub-category line starts a new item
_CONNECTORS = ("and", "or", "of", "&", ",", "(", "/", "-")


@dataclass
class Category:
    number: int
    description: str
    group: Optional[str] = None
    sub_categories: List[str] = field(default_factory=list)
    client: Optional[str] = None
    version: Optional[str] = None
    requirements: str = ""          # optional: what this category's sheet must cover
    # Set by the team per category, not read from the notice.
    eligibility: List[str] = field(default_factory=list)   # documents asked in the Eligibility section
    gate_question: bool = False     # the Organization type question gates the sheet
    applies_to: List[str] = field(default_factory=list)    # organization types this category accepts
    # The client's own requirements from the draft's columns (spec 07): asked by rules in generation
    licence: List[str] = field(default_factory=list)       # licence / contractor grade -> Eligibility
    hr: List[str] = field(default_factory=list)            # key staff -> Human Resource
    technical: List[str] = field(default_factory=list)     # documents and equipment -> Technical Specifications

    def code(self, prefix: str) -> str:
        """Sheet code for this category, e.g. prefix 'ERCS-ET-2026-' -> 'ERCS-ET-2026-019'. A
        prefix ending in a letter or digit gets a hyphen: 'PSI-ET-2026' -> 'PSI-ET-2026-046',
        never 'PSI-ET-2026046'."""
        sep = "-" if prefix and prefix[-1].isalnum() else ""
        return f"{prefix}{sep}{self.number:03d}"

    @property
    def kind(self) -> str:
        return category_kind(self.description)


def category_kind(description: str) -> str:
    """Coarse procurement kind from the description's wording.

    Drives the default marks split and which category-specific sections apply
    (goods sheets carry Technical Specifications; consultancy weights Trade
    Reference). A heuristic, so it is shown to the reviewer, never hidden.
    """
    d = (description or "").lower()
    if "consultan" in d or "advisory" in d or "audit" in d or "research" in d \
            or "study" in d or "studies" in d or "certification" in d:
        return "consultancy"
    service = re.search(r"\b(installation|maintenance|repair|servic)", d)
    if d.startswith("supply") and not service:
        return "goods"
    if re.search(r"\b(works?|construction|drilling|renovation|civil|electro-mechanical)\b", d):
        return "works"
    goods = d.startswith("supply") or re.search(r"\b(equipment|items|materials|supplies)\b", d)
    if goods and not service:
        return "goods"
    if goods:
        return "goods+service"
    return "service"


def _merge_close(values, tol):
    out = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _column_bounds(page, words, prev=None):
    """Row separators and column geometry for one table page.

    Row separators are the thin border rectangles spanning the '#' column; the
    right edge of those rectangles is where the description column starts.
    Continuation pages repeat no header, so geometry carries over via `prev`.
    Returns (separators, geometry) where geometry is (hash_x0, hash_x1, sub_x).
    """
    hash_w = next((w for w in words if w["text"] == "#"), None)
    sub_w = next((w for w in words if w["text"] == "SUB-CATEGORY"), None)
    if hash_w is not None:
        geom = (hash_w["x0"], hash_w["x1"], sub_w["x0"] - 1 if sub_w else _SUB_X)
    elif prev is not None:
        geom = prev
    else:
        return [], None
    hx0, hx1, _ = geom
    rules = [r for r in page.rects
             if r["height"] < 3 and r["x0"] < hx0 and r["x1"] > hx1 and r["x1"] - r["x0"] < 80]
    return [_merge_close([r["top"] for r in rules], _SEP_MERGE),
            max((r["x1"] for r in rules), default=_DESC_X)], geom


def _lines(words):
    """Group words into lines by their top coordinate, in reading order."""
    rows: Dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w["top"]), []).append(w)
    keys = _merge_close(rows.keys(), 2)
    merged: Dict[int, list] = {k: [] for k in keys}
    for k, ws in rows.items():
        target = min(keys, key=lambda x: abs(x - k))
        merged[target].extend(ws)
    return [(y, " ".join(w["text"] for w in sorted(merged[y], key=lambda w: w["x0"])))
            for y in sorted(merged)]


def _split_sub_categories(lines):
    items: List[str] = []
    prev_y = None
    for y, text in lines:
        cont = (
            items
            and prev_y is not None
            and (
                items[-1].lower().endswith(_CONNECTORS)
                or text[:1].islower()
                or y - prev_y <= _WRAP_GAP
            )
        )
        if cont:
            items[-1] = f"{items[-1]} {text}"
        else:
            items.append(text)
        prev_y = y
    return [re.sub(r"\s+", " ", i).strip() for i in items]


def _clean_desc(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+([),.])", r"\1", text)


def extract_categories(pdf_path: str, client: Optional[str] = None,
                       version: Optional[str] = None) -> List[Category]:
    cats: List[Category] = []
    group = None
    geom = None
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            bounds, geom = _column_bounds(page, words, geom)
            if not bounds or len(bounds[0]) < 2:
                continue
            seps, desc_x = bounds
            sub_x = geom[2]
            for top, bottom in zip(seps, seps[1:]):
                band = [w for w in words if top - 1 <= (w["top"] + w["bottom"]) / 2 < bottom]
                if not band:
                    continue
                num_words = [w for w in band if w["x1"] <= desc_x + 2]
                desc_words = [w for w in band if desc_x - 2 < w["x0"] < sub_x]
                sub_words = [w for w in band if w["x0"] >= sub_x]
                desc = _clean_desc(" ".join(t for _, t in _lines(desc_words)))
                if desc.startswith("CATEGORY DESCRIPTION"):
                    continue
                num = next((int(w["text"]) for w in num_words if w["text"].isdigit()), None)
                if num is None:
                    heading = _clean_desc(" ".join(t for _, t in _lines(band)))
                    if heading and heading.upper() == heading:
                        group = heading
                    elif cats and heading:
                        # A band without a number continues the previous category
                        # across a page break.
                        cats[-1].sub_categories += _split_sub_categories(_lines(sub_words))
                    continue
                cats.append(Category(
                    number=num,
                    description=desc,
                    group=group,
                    sub_categories=_split_sub_categories(_lines(sub_words)),
                    client=client,
                    version=version,
                ))
    return cats


def check_sequence(cats: List[Category]) -> List[str]:
    """Missing or repeated numbers usually mean the layout parse slipped."""
    nums = [c.number for c in cats]
    problems = []
    if nums:
        missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
        if missing:
            problems.append(f"missing category numbers: {missing}")
    dups = sorted({n for n in nums if nums.count(n) > 1})
    if dups:
        problems.append(f"repeated category numbers: {dups}")
    return problems


def diff(old: List[Category], new: List[Category]) -> List[str]:
    """What changed between two versions of a notice's category table."""
    a = {c.number: c for c in old}
    b = {c.number: c for c in new}
    out = []
    for n in sorted(set(a) | set(b)):
        if n not in b:
            out.append(f"{n}: removed ({a[n].description})")
        elif n not in a:
            out.append(f"{n}: added ({b[n].description})")
        else:
            for f in ("description", "group", "sub_categories", "eligibility", "gate_question", "applies_to",
                      "licence", "hr", "technical"):
                if getattr(a[n], f) != getattr(b[n], f):
                    out.append(f"{n}: {f} changed\n    was: {getattr(a[n], f)}\n    now: {getattr(b[n], f)}")
    return out


TEAM_FIELDS = ("sub_categories", "requirements", "eligibility", "gate_question", "applies_to", "licence", "hr", "technical")


def keep_team_edits(new: List[Category], applied: List[Category], table: List[Category]) -> List[str]:
    """Re-applying a refinement must not undo what the team set on the categories table since
    the last apply (PSI: a re-apply dropped a gate and four categories' technical items). An
    edit is a field where the table differs from what that apply produced; it is carried onto
    the category with the same description in `new`. Returns what was kept, one line each."""
    key = lambda c: " ".join(c.description.lower().split())
    before, now = {key(c): c for c in applied}, {key(c): c for c in table}
    kept = []
    for c in new:
        b, t = before.get(key(c)), now.get(key(c))
        if b is None or t is None:
            continue
        for f in TEAM_FIELDS:
            if getattr(t, f) != getattr(b, f) and getattr(c, f) != getattr(t, f):
                setattr(c, f, getattr(t, f))
                kept.append(f"{c.number} {c.description[:50]}: {f} kept as the team set it")
    return kept


def save(cats: List[Category], path: str):
    if path.endswith(".json"):
        with open(path, "w") as fh:
            json.dump([asdict(c) for c in cats], fh, indent=2, ensure_ascii=False)
        return
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["number", "group", "description", "sub_categories", "kind", "client", "version",
                    "requirements", "eligibility", "gate_question", "applies_to", "licence", "hr", "technical"])
        for c in cats:
            w.writerow([c.number, c.group, c.description, " | ".join(c.sub_categories),
                        c.kind, c.client, c.version, c.requirements, " | ".join(c.eligibility),
                        "TRUE" if c.gate_question else "", ", ".join(c.applies_to), " | ".join(c.licence),
                        " | ".join(c.hr), " | ".join(c.technical)])


def load(path: str) -> List[Category]:
    if path.endswith(".json"):
        with open(path) as fh:
            return [Category(**d) for d in json.load(fh)]
    out = []
    parts = lambda text: [s.strip() for s in (text or "").split("|") if s.strip()]
    with open(path, newline="") as fh:
        for d in csv.DictReader(fh):
            out.append(Category(
                number=int(d["number"]),
                description=d["description"],
                group=d["group"] or None,
                sub_categories=[s for s in d["sub_categories"].split(" | ") if s],
                client=d.get("client") or None,
                version=d.get("version") or None,
                requirements=(d.get("requirements") or "").strip(),
                eligibility=parts(d.get("eligibility")),
                gate_question=(d.get("gate_question") or "").strip().lower() in ("true", "yes", "1"),
                applies_to=[s.strip() for s in (d.get("applies_to") or "").split(",") if s.strip()],
                licence=parts(d.get("licence")), hr=parts(d.get("hr")), technical=parts(d.get("technical")),
            ))
    return out
