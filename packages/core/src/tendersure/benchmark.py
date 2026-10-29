"""The client's benchmark criteria (spec 08): sample sheets a client sends
with its categories, one tab per kind of category, as the template and the
marks to build from.

A benchmark is the client's instruction for this run, not history: it is
never added to the bank. Each tab belongs to the exercise's client whatever
code or client name it carries. A tab whose marks do not add up to 100 is
refused, with its total, so the client's file is fixed rather than guessed
at; other findings (a row with no answer type) are listed and left to
generation's corrections and its pre-write check.

Groups of the categories table are mapped to tabs in a small table the team
corrects: `group | kind | tab`. The system proposes it - a shared word
between group and tab name first, else the tab whose distinctive questions
best fit the group's categories - and keeps every line already in the table.
"""

import csv
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .parser import Sheet, normalise, parse_workbook
from .similarity import TfidfEmbedder, cosine
from .check import check_standard_blocks
from .validate import validate_sheet

log = logging.getLogger("tendersure")

TOTAL = 100
_MARKS_RULES = {"SUM-SPLIT", "SUM-NO-TOTAL", "SUM-TOTAL-SPLIT", "SUM-NOT-100", "SUM-MISMATCH", "SUM-ROWS"}
_STOP = {"and", "of", "the", "for", "with", "services", "service", "supply", "supplies", "provision", "items",
         "related", "other"}
# What each kind of category is typically asked, to tell apart tabs that share a name
_KIND_WORDS = {"goods": "manufacturer distributor authorisation warranty goods delivery stock after-sales",
               "goods+service": "manufacturer distributor warranty installation maintenance after-sales",
               "works": "construction works site engineer foreman equipment vehicles ownership rent skilled",
               "consultancy": "consultant cv curriculum qualification experience methodology assignment",
               "service": "service delivery staff locations scope"}
MAP_HEADERS = ["group", "kind", "tab", "source"]
PROPOSED, TEAM = "proposed", "team"


@dataclass
class Tab:
    name: str
    sheet: Sheet
    total: float
    refused: List[str] = field(default_factory=list)      # why it is not used
    warnings: List[str] = field(default_factory=list)     # left to generation's corrections

    @property
    def usable(self) -> bool:
        return not self.refused


@dataclass
class MapLine:
    group: str
    kind: str              # "" for the whole group; else a category kind (goods, works...)
    tab: str
    source: str = PROPOSED  # proposed by the system, or set by the team (kept as it is)


def load(path: str, client: str) -> List[Tab]:
    """Every tab of the workbook as the client's, checked."""
    out = []
    for s in parse_workbook(path):
        s.client, s.code = client, None
        s.description = s.description if s.description and s.name else s.name
        total = sum(r.marks or 0 for r in s.rows)
        findings = validate_sheet(s) + check_standard_blocks(s)
        refused = [f"marks total {total:g}, not {TOTAL}"] if abs(total - TOTAL) > 1e-6 else []
        refused += sorted({f"{f.rule}: {f.message}" for f in findings
                           if f.rule in _MARKS_RULES and f.rule != "SUM-NOT-100"})
        warnings = [f"{rule} on {n} row(s)" for rule, n in _count(f.rule for f in findings
                                                                   if f.severity == "error"
                                                                   and f.rule not in _MARKS_RULES).items()]
        out.append(Tab(s.name, s, total, refused, warnings))
    return out


def _count(xs) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def report(tabs: List[Tab]) -> List[str]:
    lines = []
    for t in tabs:
        if t.usable:
            lines.append(f"benchmark: {t.name}: usable" + (f" ({'; '.join(t.warnings)})" if t.warnings else ""))
        else:
            lines.append(f"benchmark: {t.name}: NOT USED - {'; '.join(t.refused)}; fix the tab in the client's file")
    return lines


# --- the map --------------------------------------------------------------------

def _words(text: str, drop: set) -> List[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and w not in drop and len(w) > 1]


def _name_score(group: str, tab: str, client: str) -> float:
    """A shared word counts 1, a tab word that begins a group word ("Eng" for
    Engineering) counts 0.5."""
    gw, tw = _words(group, set()), _words(tab, {client.lower()})
    score = 0.0
    for t in tw:
        if t in gw:
            score += 1
        elif len(t) >= 3 and any(g.startswith(t) for g in gw):
            score += 0.5
    return score


def _distinctive(tabs: List[Tab]) -> Dict[str, str]:
    """Each tab's questions that most other tabs do not ask: what sets it apart."""
    seen: Dict[str, int] = {}
    for t in tabs:
        for k in {normalise(r.criteria) for r in t.sheet.rows}:
            seen[k] = seen.get(k, 0) + 1
    limit = max(1, len(tabs) // 2) if len(tabs) > 2 else 1
    return {t.name: " ".join(r.criteria for r in t.sheet.rows if seen[normalise(r.criteria)] <= limit)
            for t in tabs}


def _category_text(cats, kind_words: bool = False) -> str:
    parts = []
    for c in cats:
        parts += [c.description] + list(c.sub_categories) + list(c.licence) + list(c.hr) + list(c.technical)
        if kind_words:
            parts.append(_KIND_WORDS.get(c.kind, ""))
    return " ".join(parts)


def family(kind: str) -> str:
    """Kinds as the map splits them: goods with services counts as goods."""
    return "goods" if kind.startswith("goods") else kind


def propose_map(categories, tabs: List[Tab], client: str, keep: Optional[List[MapLine]] = None) -> List[MapLine]:
    """A line per group: the tab sharing a word with its name, else the tab
    whose distinctive questions fit its categories best. Where several tabs
    share the name equally, the group's kinds may each get their own line.
    Groups already in `keep` stay as they are. Refused tabs are mapped too:
    once the client fixes them, they are used."""
    keep = list(keep or [])

    def fit(cats, pool: List[Tab], kind_words: bool) -> str:
        """The pool's tab whose own questions (those the rest of the pool lacks)
        fit the categories best; with nothing to go on, the plainest tab."""
        texts = _distinctive(pool)
        want = _category_text(cats, kind_words)
        emb = TfidfEmbedder().fit(list(texts.values()) + [want])
        score = {t.name: cosine(emb.vector(want), emb.vector(texts[t.name])) if texts[t.name] else 0.0
                 for t in pool}
        if max(score.values()) < 0.02:
            return min(pool, key=lambda t: len(texts[t.name])).name
        return max(pool, key=lambda t: score[t.name]).name

    groups: Dict[str, List] = {}
    for c in categories:
        groups.setdefault(c.group or "", []).append(c)
    out = keep[:]
    for group, cats in groups.items():
        if not tabs or any(m.group == group for m in keep):
            continue
        scores = {t.name: _name_score(group, t.name, client) for t in tabs}
        top = max(scores.values())
        pool = [t for t in tabs if scores[t.name] == top] if top > 0 else tabs
        if len(pool) == 1:
            out.append(MapLine(group, "", pool[0].name))
            continue
        by_kind: Dict[str, List] = {}
        for c in cats:
            by_kind.setdefault(family(c.kind), []).append(c)
        if top == 0:                          # no name to go by: what the group buys decides
            out.append(MapLine(group, "", fit(cats, pool, False)))
            continue
        picks = {k: fit(cs, pool, True) for k, cs in sorted(by_kind.items())}
        if len(set(picks.values())) == 1:
            out.append(MapLine(group, "", next(iter(picks.values()))))
        else:
            out += [MapLine(group, k, tab) for k, tab in picks.items()]
    return out


def tab_for(category, lines: List[MapLine]) -> Optional[MapLine]:
    """The map line for a category: its group and kind, else its whole group."""
    group, kind = category.group or "", family(category.kind)
    return next((m for m in lines if m.group == group and m.kind and family(m.kind) == kind), None) or \
        next((m for m in lines if m.group == group and not m.kind), None)


def load_map(path: str) -> List[MapLine]:
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [MapLine((d.get("group") or "").strip(), (d.get("kind") or "").strip(), (d.get("tab") or "").strip(),
                        (d.get("source") or TEAM).strip() or TEAM)
                for d in csv.DictReader(fh) if (d.get("tab") or "").strip()]


def save_map(lines: List[MapLine], path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(MAP_HEADERS)
        for m in lines:
            w.writerow([m.group, m.kind, m.tab, m.source])


def skeletons(categories, tabs: List[Tab], lines: List[MapLine]) -> Tuple[Dict[int, Sheet], Dict[int, str]]:
    """By category number: the tab to build from, and for the others why the
    bank is used instead."""
    by_name = {t.name: t for t in tabs}
    use, why = {}, {}
    for c in categories:
        m = tab_for(c, lines)
        t = by_name.get(m.tab) if m else None
        if m is None:
            why[c.number] = f"group '{c.group or '-'}' is not mapped to a benchmark tab: built from the bank"
        elif t is None:
            why[c.number] = f"benchmark tab '{m.tab}' is not in the workbook: built from the bank"
        elif not t.usable:
            why[c.number] = f"benchmark tab '{t.name}' not used ({'; '.join(t.refused)}): built from the bank"
        else:
            use[c.number] = t.sheet
    return use, why
