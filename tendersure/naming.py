"""Archive Short Names and Acronyms on a batch of sheets (spec 02 G8).

The glossary supplies the names past sheets used. Questions it lacks are
drafted: by the model when one is configured (one call per client for the
whole batch), else by rules. Drafts go into the glossary, so each question is
drafted once and the team can confirm or change it there. Code keeps the last
word: a model draft that breaks the shape rules (1-4 words; acronym 2-6
capitals or digits) is replaced by the rule draft.

A question that repeats in a sheet (an ID for each shareholder) is numbered
on both names: "Shareholder ID or passport 1" / "SIP 1", as references are.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import glossary as gl
from .generate import Plan, PlannedRow
from .llm import LLMError, make_llm
from .trust import context_key
from .validate import needs_names

_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
_LEAD = {"please", "kindly", "attach", "upload", "provide", "submit", "include", "a", "an", "the", "your",
         "valid", "copy", "copies", "of", "scanned", "current", "recent", "evidence"}
_CUT = re.compile(r"\(|,|;|\s(?:e\.?g\.?|for|from|issued|if|that|which|showing|including|by)\s", re.I)
_ACRONYM = re.compile(r"[A-Z0-9]{2,6}")


@dataclass
class Need:
    client: str
    key: str
    example: str
    short_name: str = ""        # known already; only the acronym is wanted


def rule_names(text: str, short_name: str = "") -> Tuple[str, str]:
    """A plain draft from the question text: the document noun phrase, up to
    four words, and its initials."""
    if not short_name:
        words = _CUT.split(text.strip(), maxsplit=1)[0].split()
        while words and words[0].lower().strip(":.") in _LEAD:
            words.pop(0)
        words = words[:4] or text.split()[:4]
        short_name = " ".join(w if w.isupper() or (i and w.lower() in _SMALL) else w[:1].upper() + w[1:]
                              for i, w in enumerate(words)).strip(" .:?")
    letters = "".join(w if w.isupper() and len(w) <= 4 else w[0] for w in short_name.split()
                      if w.lower() not in _SMALL and w[0].isalnum()).upper()
    acronym = letters[:6] if len(letters) >= 2 else (short_name.replace(" ", "")[:3].upper() or "DOC")
    return short_name, acronym


def _valid(short_name: str, acronym: str) -> bool:
    return 1 <= len(short_name.split()) <= 4 and len(short_name) <= 45 and bool(_ACRONYM.fullmatch(acronym))


class RuleDrafter:
    status = gl.RULE_DRAFTED
    redraws_rules = False

    def draft(self, needs: List[Need]) -> List[gl.Entry]:
        return [gl.Entry(n.client, n.key, n.example, *rule_names(n.example, n.short_name), status=self.status)
                for n in needs]


SYSTEM = (
    "You name documents that suppliers upload in a supplier prequalification questionnaire. "
    "For each numbered question give:\n"
    "- short_name: 1 to 4 words, Title Case, naming the document itself so it can be filed "
    "(e.g. 'TIN Certificate', 'Welding Machine Ownership', 'Structural Engineer CV');\n"
    "- acronym: 2 to 6 capital letters or digits (e.g. 'TIN', 'WMO', 'SECV').\n"
    "Where a short name is given, keep it exactly and give only its acronym. "
    "Make every short name and acronym in the list distinct. Follow the style of the examples."
)
SCHEMA = {"type": "object", "required": ["names"], "properties": {"names": {"type": "array", "items": {
    "type": "object", "required": ["id", "short_name", "acronym"],
    "properties": {"id": {"type": "integer"}, "short_name": {"type": "string"}, "acronym": {"type": "string"}}}}}}


class ModelDrafter:
    """Drafts names with any configured model; rules for whatever it gets wrong."""
    status = gl.DRAFTED
    redraws_rules = True

    def __init__(self, llm=None, model: Optional[str] = None, examples: Optional[List[gl.Entry]] = None):
        self.llm = llm or make_llm(model=model)
        self.examples = examples or []

    def _prompt(self, client: str, needs: List[Need]) -> str:
        shown = [e for e in self.examples if e.short_name and e.acronym and e.client in (client, gl.ANY_CLIENT)]
        lines = ["EXAMPLES (question -> short_name | acronym):"]
        lines += [f"  {e.example} -> {e.short_name} | {e.acronym}" for e in shown[:15]]
        lines += ["", "QUESTIONS:"]
        lines += [f"  {i} | {n.example}" + (f" | given short_name: {n.short_name}" if n.short_name else "")
                  for i, n in enumerate(needs)]
        return "\n".join(lines)

    def draft(self, needs: List[Need]) -> List[gl.Entry]:
        out = []
        by_client: Dict[str, List[Need]] = defaultdict(list)
        for n in needs:
            by_client[n.client].append(n)
        for client, group in by_client.items():
            try:
                raw = self.llm.complete_json(SYSTEM, self._prompt(client, group), SCHEMA, "document_names",
                                             client=client, max_tokens=8000)
                answers = {a["id"]: a for a in raw["names"]}
            except LLMError as e:
                print(f"naming: no model drafts for {client} ({e}); drafted by rules")
                answers = {}
            for i, n in enumerate(group):
                a = answers.get(i, {})
                short = n.short_name or a.get("short_name", "").strip()
                acronym = a.get("acronym", "").strip().upper()
                if a and _valid(short, acronym):
                    out.append(gl.Entry(client, n.key, n.example, short, acronym, status=gl.DRAFTED))
                else:
                    out.append(gl.Entry(client, n.key, n.example, *rule_names(n.example, n.short_name),
                                        status=gl.RULE_DRAFTED))
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


def _numbered(name: str, n: int) -> str:
    return f"{name} {n}" if name else name


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


def name_documents(plans: List[Plan], glossary: gl.Glossary, drafter) -> List[gl.Entry]:
    """Draft what the glossary lacks (adding it there), then name every sheet.
    Returns the new glossary entries."""
    drafted = drafter.draft(needs(plans, glossary, drafter.redraws_rules))
    for e in drafted:
        glossary.add(e)
    for plan in plans:
        fill(plan, glossary)
    return drafted
