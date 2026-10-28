"""Archive Short Names and Acronyms on a batch of sheets (spec 02 G8).

The glossary supplies the names past sheets used; names a row already has
are kept. A question the glossary cannot name gets a drafted Short Name - by
the model when one is configured (one call per client for the whole batch),
else by rules - and its Acronym is always built from the Short Name by the
team's letter-combining rule (`acronym_for`), never invented. Drafts go into
the glossary, so each question is drafted once and the team can confirm or
change it there.

A question that repeats in a sheet (an ID for each shareholder) is numbered
on both names: "Shareholder ID or passport 1" / "SIDP 1", as references are.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import glossary as gl
from . import grouping
from .generate import Plan, PlannedRow
from .llm import LLMError, make_llm
from .trust import context_key
from .validate import needs_names

_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
_LEAD = {"please", "kindly", "attach", "upload", "provide", "submit", "include", "a", "an", "the", "your",
         "valid", "copy", "copies", "of", "scanned", "current", "recent", "evidence"}
_CUT = re.compile(r"\(|,|;|\s(?:e\.?g\.?|for|from|issued|if|that|which|showing|including|by)\s", re.I)
_DOCUMENT_WORDS = {"certificate", "certificates", "license", "licence", "licenses", "policy", "form",
                   "membership", "document"}


@dataclass
class Need:
    client: str
    key: str
    example: str
    short_name: str = ""        # known already; its acronym follows from it


def _is_abbreviation(word: str) -> bool:
    letters = word.replace("/", "").replace("-", "")
    return len(letters) >= 2 and letters.isupper()


def acronym_for(short_name: str) -> str:
    """The team's way of writing an acronym (agreed 2026-10-28), which their
    own sheets follow: the initials of the Short Name's words, small words
    skipped (Tax Compliance Certificate -> TCC); a word already in capitals,
    or a number, stays whole (Structural Engineer CV -> SECV); after such an
    abbreviation a closing document word is dropped (TIN Certificate -> TIN,
    SHE Policy -> SHE); a one-word name gives its first three letters
    (Accounts -> ACC, Reference -> REF)."""
    words = [w.strip("()[],.:") for w in short_name.split()]
    words = [w for w in words if w and w.lower() not in _SMALL]
    if len(words) > 1 and words[-1].lower() in _DOCUMENT_WORDS and any(_is_abbreviation(w) for w in words):
        words = words[:-1]
    if not words:
        return ""
    if len(words) == 1:
        w = words[0]
        return w.split("/")[0] if _is_abbreviation(w) else w[:3].upper()
    return "".join(w.split("/")[0] if _is_abbreviation(w) or w.isdigit() else w[0] for w in words).upper()


def rule_short_name(text: str) -> str:
    """A plain Short Name from the question text: the document noun phrase,
    up to four words."""
    words = _CUT.split(text.strip(), maxsplit=1)[0].split()
    while words and words[0].lower().strip(":.") in _LEAD:
        words.pop(0)
    words = words[:4] or text.split()[:4]
    return " ".join(w if w.isupper() or (i and w.lower() in _SMALL) else w[:1].upper() + w[1:]
                    for i, w in enumerate(words)).strip(" .:?")


def _entry(n: Need, short_name: str, status: str) -> gl.Entry:
    return gl.Entry(n.client, n.key, n.example, short_name, acronym_for(short_name), status=status)


class RuleDrafter:
    redraws_rules = False

    def draft(self, needs: List[Need]) -> List[gl.Entry]:
        return [_entry(n, n.short_name, gl.DRAFTED) if n.short_name else
                _entry(n, rule_short_name(n.example), gl.RULE_DRAFTED) for n in needs]


SYSTEM = (
    "You name documents that suppliers upload in a supplier prequalification questionnaire. "
    "For each numbered question give short_name: 1 to 4 words, Title Case, naming the document "
    "itself so it can be filed (e.g. 'TIN Certificate', 'Welding Machine Ownership', "
    "'Structural Engineer CV'). Keep standard abbreviations in capitals (TIN, VAT, CV, ISO). "
    "Make every short name in the list distinct. Follow the style of the examples."
)
SCHEMA = {"type": "object", "required": ["names"], "properties": {"names": {"type": "array", "items": {
    "type": "object", "required": ["id", "short_name"],
    "properties": {"id": {"type": "integer"}, "short_name": {"type": "string"}}}}}}


CHUNK = 40      # questions per model call (spec 04 L5)


def _chunks(items: list, size: int):
    return [items[i:i + size] for i in range(0, len(items), size)]


class ModelDrafter:
    """Short Names from any configured model; the rule for whatever it gets
    wrong. A question that already has a Short Name needs no model. Questions
    go CHUNK at a time; a failed call costs only its own chunk."""
    redraws_rules = True
    chunk = CHUNK

    def __init__(self, llm=None, model: Optional[str] = None, examples: Optional[List[gl.Entry]] = None):
        self.llm = llm or make_llm(model=model)
        self.examples = examples or []

    def _prompt(self, client: Optional[str], needs: List[Need]) -> str:
        shown = [e for e in self.examples if e.short_name and e.client in (client, gl.ANY_CLIENT)]
        lines = ["EXAMPLES (question -> short_name):"]
        lines += [f"  {e.example} -> {e.short_name}" for e in shown[:15]]
        lines += ["", "QUESTIONS:"] + [f"  {i} | {n.example}" for i, n in enumerate(needs)]
        return "\n".join(lines)

    def _ask(self, client: Optional[str], group: List[Need]) -> Dict[int, str]:
        try:
            raw = self.llm.complete_json(SYSTEM, self._prompt(client, group), SCHEMA, "document_names",
                                         client=client, max_tokens=8000)
            return {a["id"]: a["short_name"].strip() for a in raw["names"]}
        except LLMError as e:
            print(f"naming: no model drafts for {client} ({e}); drafted by rules")
            return {}

    def draft(self, needs: List[Need]) -> List[gl.Entry]:
        out = [_entry(n, n.short_name, gl.DRAFTED) for n in needs if n.short_name]
        by_client: Dict[Optional[str], List[Need]] = defaultdict(list)
        for n in needs:
            if not n.short_name:
                by_client[n.client].append(n)
        for client, group in by_client.items():
            for part in _chunks(group, self.chunk):
                out += self._draft_part(client, part)
        return out

    def _draft_part(self, client: Optional[str], group: List[Need]) -> List[gl.Entry]:
        out = []
        answers = self._ask(client, group)
        for i, n in enumerate(group):
            short = answers.get(i, "")
            if 1 <= len(short.split()) <= 4 and len(short) <= 45:
                out.append(_entry(n, short, gl.DRAFTED))
            else:
                out.append(_entry(n, rule_short_name(n.example), gl.RULE_DRAFTED))
        return out


def _named_rows(plan: Plan) -> List[Tuple[PlannedRow, str]]:
    qs = plan.questions
    rows = [p.row for p in qs]
    return [(p, context_key(rows, i)) for i, p in enumerate(qs) if needs_names(p.row)]


def _resolve(row, e: Optional[gl.Entry], n: int) -> Tuple[str, str, List[str], bool]:
    """The row's own names (without an occurrence number), completed from the
    glossary entry; which of them the glossary supplied; and whether the row's
    own names were numbered ('Reference 1')."""
    (short, num_s), (acronym, num_a) = gl.unnumbered(row.archive_short_name, n), gl.unnumbered(row.acronym, n)
    took = []
    if e and not short and e.short_name:
        short, took = e.short_name, took + ["short name"]
    if e and not acronym and e.acronym:
        acronym, took = e.acronym, took + ["acronym"]
    return short, acronym, took, num_s or num_a


def needs(plans: List[Plan], glossary: gl.Glossary, redraw_rules: bool) -> List[Need]:
    """Questions the glossary cannot fully name, once per client and question."""
    out: Dict[Tuple[str, str], Need] = {}
    for plan in plans:
        client, seen = plan.target.client, Counter()
        for p, key in _named_rows(plan):
            seen[key] += 1
            e = glossary.lookup(client, key)
            if e and e.status == gl.RULE_DRAFTED and redraw_rules:
                e = None
            short, acronym, _, _ = _resolve(p.row, e, seen[key])
            if not short or not acronym:
                out.setdefault((client, key), Need(client, key, p.row.criteria, short))
    return list(out.values())


def _numbered(name: str, n: Optional[int]) -> str:
    return f"{name} {n}" if name and n else name


def fill(plan: Plan, glossary: gl.Glossary):
    """Names on every row that needs them; repeats numbered; acronyms unique."""
    pairs = _named_rows(plan)
    total, seen = Counter(k for _, k in pairs), Counter()
    for p, key in pairs:
        seen[key] += 1
        n, e = seen[key], glossary.lookup(plan.target.client, key)
        short, acronym, took, was_numbered = _resolve(p.row, e, n)
        if e and e.short_name and p.row.archive_short_name and short != e.short_name:
            p.note(f"short name '{short}' differs from the glossary's '{e.short_name}'", adapted=False)
        if total[key] > 1 or was_numbered or (e and e.numbered):
            short, acronym = _numbered(short, n), _numbered(acronym, n)
        if took:
            how = ("drafted - confirm it in data/glossary.csv" if e.status in (gl.DRAFTED, gl.RULE_DRAFTED)
                   else "from the glossary")
            p.note(f"{' and '.join(took)} {how}", adapted=False)
        p.row.archive_short_name, p.row.acronym = short or None, acronym or None
    _unique_acronyms(plan)


def _unique_acronyms(plan: Plan):
    used: Dict[str, str] = {}
    for p, key in _named_rows(plan):
        a = p.row.acronym
        if not a:
            continue
        if a in used and used[a] != key:
            k = 2
            while f"{a}{k}" in used:
                k += 1
            p.note(f"acronym {a} -> {a}{k}: '{a}' is taken by another document in this sheet", adapted=False)
            a = p.row.acronym = f"{a}{k}"
        used.setdefault(a, key)


def reuse_similar(needs_: List[Need], glossary: gl.Glossary) -> Tuple[List[gl.Entry], List[Need]]:
    """A question with no Short Name takes the names of a confirmed rewording
    of it (spec 04 L2) - the client's own first - as a draft, since a
    rewording can change the document. Returns (reused entries, still needed)."""
    confirmed = [e for e in glossary.entries if e.status == gl.CONFIRMED and e.short_name]
    if not confirmed:
        return [], needs_
    keys = {e.question_key for e in confirmed}
    rep = grouping.group(list(dict.fromkeys([e.question_key for e in confirmed] + [n.key for n in needs_])),
                         apart=lambda a, b: a in keys and b in keys)
    by_group: Dict[str, List[gl.Entry]] = defaultdict(list)
    for e in confirmed:
        by_group[rep[e.question_key]].append(e)
    reused, rest = [], []
    for n in needs_:
        group_ = by_group.get(rep[n.key], [])
        twins = [e for e in group_ if e.client == n.client] + [e for e in group_ if e.client != n.client]
        if n.short_name or not twins:
            rest.append(n)
        else:
            t = twins[0]
            reused.append(gl.Entry(n.client, n.key, n.example, t.short_name, t.acronym, t.numbered, gl.SIMILAR))
    return reused, rest


def name_documents(plans: List[Plan], glossary: gl.Glossary, drafter) -> List[gl.Entry]:
    """Draft what the glossary lacks (adding it there), then name every sheet.
    Returns the new glossary entries."""
    reused, rest = reuse_similar(needs(plans, glossary, drafter.redraws_rules), glossary)
    drafted = reused + drafter.draft(rest)
    for e in drafted:
        glossary.add(e)
    for plan in plans:
        fill(plan, glossary)
    return drafted


def _numbered_names(e: gl.Entry, names: set) -> Tuple[str, Optional[int]]:
    """'Staff 2' is item 2 of 'Staff' when 'Staff' is itself a name; 'ISO 9001' is not."""
    base, n = gl.split_number(e.short_name)
    return (base, n) if n is not None and base.lower() in names else (e.short_name, None)


def _known_acronyms(entries: List[gl.Entry]) -> Dict[str, str]:
    """Short name -> the acronym the glossary gives it; confirmed rows first,
    then drafted, then the most used."""
    rank = {gl.CONFIRMED: 0, gl.DRAFTED: 1, gl.SEEDED: 2, gl.RULE_DRAFTED: 3}
    out: Dict[str, str] = {}
    for e in sorted(entries, key=lambda e: (rank.get(e.status, 4), -e.uses)):
        if not (e.short_name and e.acronym):
            continue
        (s_base, s_n), (a_base, a_n) = gl.split_number(e.short_name), gl.split_number(e.acronym)
        if s_n is not None and s_n == a_n:          # Reference 2 / REF 2 -> Reference / REF
            out.setdefault(s_base.lower(), a_base)
        out.setdefault(e.short_name.lower(), e.acronym)
    return out


def complete_glossary(glossary: gl.Glossary, drafter) -> Tuple[int, int, int]:
    """Give every glossary entry both names. Drafts take the acronym the rule
    builds from their Short Name. An empty acronym takes the one the glossary
    already gives the same Short Name, so one document keeps one acronym, or
    else the rule's. An entry with no Short Name is drafted. Seeded and
    confirmed acronyms - the clients' own (COI, DAF/MAF) - are kept.
    Returns (re-derived, reused, built)."""
    names = {e.short_name.lower() for e in glossary.entries if e.short_name}
    rederived = 0
    for e in glossary.entries:
        if e.status in (gl.DRAFTED, gl.RULE_DRAFTED) and e.short_name:
            base, n = _numbered_names(e, names)
            acronym = _numbered(acronym_for(base), n)
            rederived += acronym != e.acronym
            e.acronym = acronym
    unnamed = [e for e in glossary.entries if not e.short_name]
    for d in drafter.draft([Need(None if e.client == gl.ANY_CLIENT else e.client, e.question_key, e.example)
                            for e in unnamed]):
        e = next(x for x in unnamed if x.question_key == d.question_key)
        e.short_name, e.acronym, e.status = d.short_name, e.acronym, d.status
    known = _known_acronyms(glossary.entries)
    reused = built = 0
    for e in glossary.entries:
        if e.acronym:
            continue
        base, n = _numbered_names(e, names)
        if base.lower() in known:
            e.acronym, reused = _numbered(known[base.lower()], n), reused + 1
        else:
            e.acronym, built = _numbered(acronym_for(base), n), built + 1
        e.status = gl.DRAFTED
    return rederived, reused, built
