"""The AI read of a checked workbook (spec 03): problems of meaning the
rules cannot see - an answer type that does not fit the question, a Yes/No
question scored the wrong way round, a question that does not fit its
category, wording that changes the meaning.

Its findings are always warnings, labelled as the model's, with a reason; it
cannot raise an error. Each distinct question is read once however many
sheets ask it (a 58-sheet batch has a few hundred distinct questions), in
chunks, anonymised like every call; answers are cached by question, so a
re-check after an edit asks only about what changed.
"""

import hashlib
import json
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import openpyxl

from .llm import LLMError, make_llm
from .parser import parse_sheet
from .validate import Finding

CHUNK = 60
CACHE = os.path.join(".cache", "check_ai.json")
KINDS = ["answer type", "yes/no scoring", "category fit", "wording"]
SYSTEM = (
    "You review questions in a supplier prequalification questionnaire after a team edited it by hand. "
    "Answer types: 1 text, 2 single select, 3 multi select, 4 Yes/No, 5 file upload. For each question, "
    "report only clear problems: an answer type that does not fit the question (e.g. 'Please attach...' as "
    "text), a Yes/No question scored so the wrong answer earns the marks, a question that does not fit the "
    "categories it is asked in, or wording errors that change the meaning. Most questions have none: return "
    "only the problems, each with a short reason."
)
SCHEMA = {"type": "object", "required": ["problems"], "properties": {"problems": {"type": "array", "items": {
    "type": "object", "required": ["id", "kind", "reason"],
    "properties": {"id": {"type": "integer"}, "kind": {"type": "string", "enum": KINDS},
                   "reason": {"type": "string"}}}}}}


def _signature(r) -> Tuple:
    return (r.criteria, r.answer_type, r.options or "", r.option_scores or "", r.marks or 0)


def _describe(sig: Tuple, categories: List[str]) -> str:
    text, at, options, scores, marks = sig
    extra = f" | options: {options} | scores: {scores}" if options else ""
    where = f" | asked in: {'; '.join(categories[:2])}" if len(categories) <= 3 else ""
    return f"{text} | type {at} | marks {marks:g}{extra}{where}"


def read(path: str, model: Optional[str] = None, llm=None, client: Optional[str] = None) -> List[Finding]:
    """Model findings for every distinct question in the workbook."""
    seen: Dict[Tuple, List[Tuple[str, int, str]]] = defaultdict(list)
    for ws in openpyxl.load_workbook(path, data_only=True).worksheets:
        s = parse_sheet(ws, os.path.basename(path))
        if s is None:
            continue
        client = client or s.client
        for r in s.rows:
            seen[_signature(r)].append((s.name, r.excel_row, s.description or ""))
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            cache = json.load(fh)
    key = lambda sig: hashlib.sha1(_describe(sig, sorted({c for _, _, c in seen[sig]})).encode()).hexdigest()[:16]
    todo = [sig for sig in seen if key(sig) not in cache]
    llm = llm or (make_llm(model=model) if todo else None)
    for part in (todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)):
        prompt = "QUESTIONS (id | question | answer type | marks | options | categories):\n" + "\n".join(
            f"  {i} | {_describe(sig, sorted({c for _, _, c in seen[sig]}))}" for i, sig in enumerate(part))
        try:
            raw = llm.complete_json(SYSTEM, prompt, SCHEMA, "check_read", client=client, max_tokens=8000)
        except LLMError as e:
            print(f"check: the AI read skipped {len(part)} questions ({e})")
            continue
        found = defaultdict(list)
        for p in raw["problems"]:
            found[p["id"]].append([p["kind"], p["reason"]])
        for i, sig in enumerate(part):
            cache[key(sig)] = found.get(i, [])
    if todo:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w") as fh:
            json.dump(cache, fh)
    out = []
    for sig, where in seen.items():
        for kind, reason in cache.get(key(sig), []):
            sheet, row, _ = where[0]
            more = f" (and {len(where) - 1} more sheets)" if len(where) > 1 else ""
            out.append(Finding(sheet, row, f"AI-{kind.upper().replace(' ', '-').replace('/', '')}", "model",
                               "warning", f"{reason}{more}", "From the AI read: check and decide."))
    return out
