"""The glossary: an Archive Short Name and Acronym for every document a sheet
asks for (spec 02 G8).

Seeded from the bank: for each question, the names its client used most,
with what other clients used as the fallback. Kept as data/glossary.csv,
which the team may edit. Repeated blocks (Reference 1, 2, 3) are stored once,
as a base name numbered per sheet. Re-seeding replaces only `seeded` rows:
mark a row `confirmed` to keep an edit; `drafted` rows are kept until the
team confirms or changes them.

Questions are keyed in context (trust.context_key), so "If yes attach a copy"
under a safety policy question is not the same document as under another.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .learned_store import GLOSSARY, resolve
from .parser import Sheet
from .trust import context_key
from .validate import needs_names

SEEDED, DRAFTED, CONFIRMED = "seeded", "drafted", "confirmed"
RULE_DRAFTED = "drafted (rules)"     # drafted without a model; redrafted when one is available
ANY_CLIENT = "*"
COLUMNS = ["client", "question_key", "example", "short_name", "acronym", "numbered", "status", "uses"]
_TRAILING_NUMBER = re.compile(r"^(.*\S)\s+(\d+)$")


@dataclass
class Entry:
    client: str
    question_key: str
    example: str
    short_name: str = ""
    acronym: str = ""
    numbered: bool = False
    status: str = SEEDED
    uses: int = 0

    def names(self, n: int = 1) -> Tuple[str, str]:
        """The pair for the n-th occurrence of the question in a sheet."""
        if not self.numbered:
            return self.short_name, self.acronym
        return (f"{self.short_name} {n}" if self.short_name else "",
                f"{self.acronym} {n}" if self.acronym else "")


def unnumbered(name: Optional[str], n: int) -> Tuple[str, bool]:
    """'Reference 2' on the 2nd occurrence -> ('Reference', True). A number
    that is not the occurrence ('ISO 9001') is part of the name."""
    name = (name or "").strip()
    m = _TRAILING_NUMBER.match(name)
    if m and int(m.group(2)) == n:
        return m.group(1), True
    return name, False


def split_number(name: str) -> Tuple[str, Optional[int]]:
    """'Staff 2' -> ('Staff', 2); a name without a trailing number -> (name, None)."""
    m = _TRAILING_NUMBER.match(name or "")
    return (m.group(1), int(m.group(2))) if m else (name or "", None)


def _document_order(e: Entry):
    """Rows for one document sit together: by acronym (REF, REF 2, REF 3),
    then name; the client's own rows before the cross-client one."""
    base, n = split_number(e.acronym)
    return (not base, base.upper(), n or 0, e.short_name.lower(), e.client == ANY_CLIENT, e.client, e.question_key)


def _entry(client: str, key: str, seen: List[Tuple[str, str, bool, str]]) -> Optional[Entry]:
    shorts = Counter(s for s, _, _, _ in seen if s)
    short = shorts.most_common(1)[0][0] if shorts else ""
    acronyms = Counter(a for s, a, _, _ in seen if a and (s == short or not short)) or \
        Counter(a for _, a, _, _ in seen if a)
    acronym = acronyms.most_common(1)[0][0] if acronyms else ""
    if not short and not acronym:
        return None
    numbered = any(num for s, _, num, _ in seen if s == short)
    return Entry(client, key, seen[0][3], short, acronym, numbered, SEEDED, len(seen))


def seed(sheets: List[Sheet]) -> List[Entry]:
    """Glossary entries from past sheets: one per client and question, plus one
    across clients ('*') as the fallback."""
    seen: Dict[Tuple[str, str], list] = defaultdict(list)
    for s in sheets:
        count = Counter()
        for i, r in enumerate(s.rows):
            if not needs_names(r):
                continue
            key = context_key(s.rows, i)
            count[key] += 1
            short, num_s = unnumbered(r.archive_short_name, count[key])
            acronym, num_a = unnumbered(r.acronym, count[key])
            obs = (short, acronym, num_s or num_a, r.criteria)
            seen[(s.client or ANY_CLIENT, key)].append(obs)
            if s.client:
                seen[(ANY_CLIENT, key)].append(obs)
    entries = [_entry(client, key, obs) for (client, key), obs in seen.items()]
    return [e for e in entries if e]


def reseed(existing: List[Entry], fresh: List[Entry]) -> List[Entry]:
    """Fresh seeded entries, except where the team has confirmed or a draft is waiting."""
    kept = [e for e in existing if e.status != SEEDED]
    taken = {(e.client, e.question_key) for e in kept}
    return kept + [e for e in fresh if (e.client, e.question_key) not in taken]


class Glossary:
    def __init__(self, entries: List[Entry]):
        self.entries = entries
        self._by = {(e.client, e.question_key): e for e in entries}

    def lookup(self, client: Optional[str], key: str) -> Optional[Entry]:
        """The client's own entry, else the cross-client one."""
        return self._by.get((client or ANY_CLIENT, key)) or self._by.get((ANY_CLIENT, key))

    def add(self, entry: Entry):
        """Add an entry, replacing one for the same client and question."""
        old = self._by.get((entry.client, entry.question_key))
        if old is not None:
            self.entries.remove(old)
        self.entries.append(entry)
        self._by[(entry.client, entry.question_key)] = entry


def load(target) -> List[Entry]:
    """From a storage driver (see learned_store) or a CSV path."""
    store, name = resolve(target, GLOSSARY)
    return [Entry(d["client"], d["question_key"], d["example"], d["short_name"], d["acronym"],
                  d["numbered"].strip().lower() == "true", d["status"] or SEEDED, int(d["uses"] or 0))
            for d in store.read(name)]


def save(entries: List[Entry], target):
    store, name = resolve(target, GLOSSARY)
    store.write(name, COLUMNS, [
        {"client": e.client, "question_key": e.question_key, "example": e.example, "short_name": e.short_name,
         "acronym": e.acronym, "numbered": "TRUE" if e.numbered else "", "status": e.status, "uses": e.uses}
        for e in sorted(entries, key=_document_order)])
