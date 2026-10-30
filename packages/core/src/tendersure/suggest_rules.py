"""Client rules proposed from the client's own files (spec 16 C): suggestions
the team adds with one click, never applied by themselves.

- From the client's sample sheets (the benchmark workbook): the marks most
  tabs give each reference sub-section; the terms the client uses where a
  known pair has two; the most technical questions any tab asks.
- From an edited copy: a change the editor made on two or more sheets - the
  same question removed, the same term replaced, the same sub-section's marks
  set.

A suggestion an existing rule already covers is left out.
"""

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from .client_rules import Rule
from .parser import Sheet, normalise, section_key

# (the term to replace, the client's term): pairs the same document goes by
TERM_PAIRS = [("LPO", "PO"), ("business permit", "trade license"), ("KRA PIN", "TIN"),
              ("VAT certificate", "VAT registration certificate")]
NEVER = {"declaration", "submission"}
REPEATED = 2          # sheets an edit must repeat on to become a rule


def _has(term: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + r"\s+".join(map(re.escape, term.split())) + r"(?![A-Za-z0-9])",
                     text, re.I) is not None


def _rows(sheet: Sheet):
    return [r for r in sheet.rows if section_key(r.section) not in NEVER]


def _ref_marks(sheet: Sheet) -> Dict[str, float]:
    out: Dict[str, float] = defaultdict(float)
    for r in sheet.rows:
        if section_key(r.section) == "trade_reference" and r.sub_section:
            out[" ".join(str(r.sub_section).split())] += r.marks or 0
    return dict(out)


def from_samples(sheets: List[Sheet], client: str) -> List[Rule]:
    if not sheets:
        return []
    out = []
    n = len(sheets)
    # reference marks: the split most tabs share, when it is most of them
    splits = Counter(tuple(sorted(_ref_marks(s).items())) for s in sheets if _ref_marks(s))
    if splits:
        split, k = splits.most_common(1)[0]
        if k * 2 > n:
            total = sum(v for _, v in split)
            out += [Rule(client, "marks", sub, f"{v:g}", "", f"suggested: {k} of {n} sample tabs give {sub} {v:g} "
                         f"(references {total:g})") for sub, v in split]
    # terms: the client's term where its tabs never use the other
    text = " ".join(r.criteria for s in sheets for r in _rows(s))
    for old, new in TERM_PAIRS:
        if _has(new, text) and not _has(old, text):
            out.append(Rule(client, "replace", old, new, "", f"suggested: the sample tabs say '{new}', never '{old}'"))
    # technical: the most questions any tab asks
    most = max(sum(section_key(r.section) == "technical" for r in s.rows) for s in sheets)
    if 0 < most <= 5:
        out.append(Rule(client, "limit", "technical", str(most), "", f"suggested: no sample tab asks more than {most} "
                        f"technical questions"))
    return out


def _phrase_change(a: str, b: str) -> Optional[tuple]:
    """'Attach the LPO copy' -> 'Attach the PO copy': ('LPO', 'PO') when one run of words changed."""
    wa, wb = a.split(), b.split()
    ops = [op for op in SequenceMatcher(None, wa, wb, autojunk=False).get_opcodes() if op[0] != "equal"]
    if len(ops) != 1 or ops[0][0] != "replace":
        return None
    _, i1, i2, j1, j2 = ops[0]
    old, new = " ".join(wa[i1:i2]).strip(".,;:()"), " ".join(wb[j1:j2]).strip(".,;:()")
    return (old, new) if old and new and i2 - i1 <= 4 and j2 - j1 <= 4 else None


def from_edits(before: Dict[str, Sheet], after: List[Sheet], client: str) -> List[Rule]:
    removed, replaced, marks = Counter(), Counter(), Counter()
    example: Dict[str, str] = {}
    for s in after:
        b = before.get(s.code)
        if b is None:
            continue
        x, y = _rows(b), _rows(s)
        a_txt, b_txt = [normalise(r.criteria) for r in x], [normalise(r.criteria) for r in y]
        seen_removed, seen_replaced = set(), set()
        for op, i1, i2, j1, j2 in SequenceMatcher(None, a_txt, b_txt, autojunk=False).get_opcodes():
            if op == "replace" and i2 - i1 == j2 - j1:
                for i, j in zip(range(i1, i2), range(j1, j2)):
                    change = _phrase_change(x[i].criteria, y[j].criteria)
                    if change:
                        seen_replaced.add(change)
            elif op in ("delete", "replace"):
                for i in range(i1, i2):
                    seen_removed.add(a_txt[i])
                    example.setdefault(a_txt[i], x[i].criteria)
        removed.update(seen_removed)
        replaced.update(seen_replaced)
        old, new = _ref_marks(b), _ref_marks(s)
        marks.update((sub, v) for sub, v in new.items() if sub in old and abs(old[sub] - v) > 1e-9)
    out = [Rule(client, "remove", example[t], "", "", f"suggested: removed on {k} sheets of the edited copy")
           for t, k in removed.items() if k >= REPEATED]
    out += [Rule(client, "replace", old, new, "", f"suggested: '{old}' changed to '{new}' on {k} sheets of the edited copy")
            for (old, new), k in replaced.items() if k >= REPEATED]
    out += [Rule(client, "marks", sub, f"{v:g}", "", f"suggested: {sub} set to {v:g} on {k} sheets of the edited copy")
            for (sub, v), k in marks.items() if k >= REPEATED]
    return out


def new_only(suggested: List[Rule], existing: List[Rule]) -> List[Rule]:
    """Suggestions no existing rule covers (same action and match), each once."""
    key = lambda r: (r.action, " ".join(r.match.lower().split()))
    have, out = {key(r) for r in existing}, []
    for r in suggested:
        if key(r) not in have:
            have.add(key(r))
            out.append(r)
    return out
