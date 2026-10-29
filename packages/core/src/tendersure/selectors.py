"""Optional model step: the model proposes, the reviewer decides, code applies.

Given a deterministic plan, the model sees only what needs judgement:

  - rows flagged as specific to the skeleton's category (may not fit)
  - suggested rows from the closest other categories (may be missing)
  - the category description and sub-categories (may need new questions)

and returns a proposal. Nothing is applied here. The proposal fills the
Decisions tab: the 'Proposed' answer on Q- lines, and A- lines listing what
to add. A blank answer accepts it; the reviewer corrects the rest; code
applies only what the reviewer accepted (review.py).

Proposals are stored in the QA report, so applying a review replays exactly
what the reviewer saw, without calling the model again.

The model never sets answer types, options, scores or upload flags on banked
rows, and never sets marks. Drafted questions carry the model's suggested
answer type, arrive with 0 marks, and are marked 'newly drafted'.

Everything sent is anonymised first (client names, sheet codes, contact
details; see privacy.py). Works with any configured backend (see llm.py).
"""

from typing import Optional

from . import brief as cb
from .llm import make_llm
from .generate import Plan
from .parser import normalise, section_key
from .schema import ANSWER_TYPES, PROTECTED_SECTIONS, SECTION_KEYS

SYSTEM = """You help a procurement team adapt a supplier prequalification questionnaire to a new category.

The draft questionnaire was copied from the client's most similar past category. Propose four things; a person reviews every proposal before anything changes.
1. FLAGGED rows that test something this category needs but name the past category's subject: REWORD them. Change only the words that tie the question to the past category (for example "specialised tools for water pumps" becomes "specialised tools for kitchen equipment"; "references, majorly mechanical projects" becomes "references, majorly network infrastructure projects"). Keep the question's meaning, structure and style; its answer type and marks stay as they are.
2. FLAGGED rows whose whole purpose does not apply to this category: DROP them (for example, a product warranty question for a consultancy service). Keep a row when in doubt. Rows in the trade reference, declaration and submission sections are never dropped; reword them if they name the wrong subject.
3. Which SUGGESTED rows the new category needs. Suggestions come from other similar categories. Propose one only if it tests something this category genuinely needs.
4. Genuinely new questions, only for a clear gap neither the draft nor the suggestions cover: a licence, certification, equipment, method or qualification specific to this category. Match the wording style of the existing questions. Most categories need zero to three; a category with no close past match may need more.

Never set marks: marks are the client's decision. Answer types: 1 text, 2 single select, 3 multi select, 4 yes/no, 5 attachment. A question asking for a document is type 5. Give options only for types 2 and 3, comma-separated. Keep each reason to one short sentence.

When the client's brief is given, it comes first: propose what it asks for this category, over what past questionnaires suggest. Set from_brief on each proposal made because of it. List under brief_not_applied what the brief asks that you cannot propose here (marks, a rule above, rows that are not flagged), with the reason. Without a brief, from_brief is false and brief_not_applied is empty."""

SECTIONS = [k for _, k in SECTION_KEYS]
# Never removed by a model (see schema.PROTECTED_SECTIONS).
PROTECTED = PROTECTED_SECTIONS
# Bump when the prompt or schema changes, so saved evaluations are not mixed up.
PROMPT_VERSION = 3

SCHEMA = {
    "type": "object",
    "properties": {
        "drop": {"type": "array", "items": {
            "type": "object",
            "properties": {"row": {"type": "integer"}, "reason": {"type": "string"}, "from_brief": {"type": "boolean"}},
            "required": ["row", "reason", "from_brief"], "additionalProperties": False}},
        "reword": {"type": "array", "items": {
            "type": "object",
            "properties": {"row": {"type": "integer"}, "criteria": {"type": "string"},
                           "reason": {"type": "string"}, "from_brief": {"type": "boolean"}},
            "required": ["row", "criteria", "reason", "from_brief"], "additionalProperties": False}},
        "add": {"type": "array", "items": {
            "type": "object",
            "properties": {"suggestion": {"type": "integer"}, "reason": {"type": "string"},
                           "from_brief": {"type": "boolean"}},
            "required": ["suggestion", "reason", "from_brief"], "additionalProperties": False}},
        "drafts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "section": {"type": "string", "enum": SECTIONS},
                "criteria": {"type": "string"},
                "answer_type": {"type": "integer", "description": "1 to 5"},
                "options": {"type": "string"},
                "reason": {"type": "string"},
                "from_brief": {"type": "boolean"},
            },
            "required": ["section", "criteria", "answer_type", "options", "reason", "from_brief"],
            "additionalProperties": False}},
        "brief_not_applied": {"type": "array", "items": {
            "type": "object",
            "properties": {"request": {"type": "string"}, "why": {"type": "string"}},
            "required": ["request", "why"], "additionalProperties": False}},
    },
    "required": ["drop", "reword", "add", "drafts", "brief_not_applied"],
    "additionalProperties": False,
}


def build_prompt(plan: Plan) -> str:
    t = plan.target
    lines = cb.prompt_lines(t.brief) + [
        f"NEW CATEGORY: {t.code}: {t.description}",
        f"Group in the client's notice: {t.group or 'unknown'}",
    ]
    if t.sub_categories:
        lines.append("Sub-categories: " + "; ".join(t.sub_categories))
    if t.requirements:
        lines.append("The client's requirements for this category (make sure the questionnaire covers "
                     "them, rewording or drafting as needed): " + t.requirements)
    lines.append(f"Draft copied from: {plan.skeleton.sheet.name}: {plan.skeleton.sheet.description}")
    if plan.weak_match:
        lines.append("NOTE: that past category has little in common with the new one; the draft is "
                     "the client's generic template. Look carefully for missing category-specific questions.")
    lines += ["", "SECTIONS IN THE DRAFT (with every question, for context):"]
    for i, p in enumerate(plan.rows):
        if p.kind != "question":
            continue
        mark = " [FLAGGED]" if p.needs_reading else ""
        lines.append(f"  row {i} | {section_key(p.row.section)} | {p.row.criteria}{mark}")
    lines += ["", "SUGGESTED ROWS:"]
    if not plan.suggestions:
        lines.append("  (none)")
    for j, s in enumerate(plan.suggestions):
        lines.append(f"  suggestion {j} | {s.section} | {s.row.criteria} | {s.reason}")
    return "\n".join(lines)


class ModelSelector:
    """Any configured model (see llm.py). Pass `llm` to inject a client."""

    def __init__(self, llm=None, model: Optional[str] = None):
        self.llm = llm or make_llm(model=model)

    def propose(self, plan: Plan) -> dict:
        """Ask the model and store a checked proposal on the plan."""
        # A proposal is a few hundred tokens plus reasoning; the ceiling is also
        # what OpenRouter reserves in credit per request, so keep it modest.
        raw = self.llm.complete_json(SYSTEM, build_prompt(plan), SCHEMA, "sheet_proposal",
                                     client=plan.target.client, max_tokens=12000)
        plan.proposal = normalise_proposal(plan, raw, getattr(self.llm, "last_model", None) or "model")
        return plan.proposal


def normalise_proposal(plan: Plan, raw: dict, model: str) -> dict:
    """Turn the model's row/suggestion indices into stable references and
    drop anything invalid: only flagged rows may be proposed for removal,
    only existing suggestions for addition, only well-formed drafts."""
    from .review import qkey
    out = {"model": model, "drop": {}, "reword": {}, "add": [], "drafts": [], "ignored": [],
           "brief_not_applied": [f"{b.get('request', '')}: {b.get('why', '')}" for b in raw.get("brief_not_applied", [])
                                 if b.get("request")]}
    why = lambda d: cb.labelled(d.get("reason", ""), d.get("from_brief"))

    def flagged(i, what):
        p = plan.rows[i] if isinstance(i, int) and 0 <= i < len(plan.rows) else None
        if p is None or p.kind != "question" or not p.needs_reading:
            out["ignored"].append(f"{what} of row {i}: only flagged rows can be changed")
            return None
        return p

    for d in raw.get("reword", []):
        p = flagged(d.get("row"), "reword")
        text = " ".join(str(d.get("criteria", "")).split())
        if p is None:
            continue
        if not text or normalise(text) == normalise(p.row.criteria):
            out["ignored"].append(f"reword of row {d.get('row')}: no new wording")
            continue
        out["reword"][qkey(section_key(p.row.section), normalise(p.row.criteria))] = \
            {"text": text, "reason": why(d)}
    for d in raw.get("drop", []):
        p = flagged(d.get("row"), "drop")
        if p is None:
            continue
        sec = section_key(p.row.section)
        k = qkey(sec, normalise(p.row.criteria))
        if sec in PROTECTED:
            out["ignored"].append(f"drop of row {d.get('row')}: {sec} rows are never removed")
            continue
        if k in out["reword"]:
            continue                      # rewording was also proposed: the gentler change wins
        out["drop"][k] = why(d)
    for a in raw.get("add", []):
        j = a.get("suggestion")
        if not (isinstance(j, int) and 0 <= j < len(plan.suggestions)):
            out["ignored"].append(f"add of unknown suggestion {j}")
            continue
        s = plan.suggestions[j]
        out["add"].append({"source": s.source, "section": s.section, "text": s.row.criteria,
                           "reason": why(a)})
    for d in raw.get("drafts", []):
        if d.get("section") not in SECTIONS or d.get("answer_type") not in ANSWER_TYPES \
                or not str(d.get("criteria", "")).strip():
            out["ignored"].append(f"malformed draft {str(d)[:80]}")
            continue
        out["drafts"].append({**{k: d[k] for k in ("section", "criteria", "answer_type", "options")},
                              "reason": why(d)})
    return out


def label_for(plan: Plan, key: str, fallback: Optional[str]) -> str:
    """Section label as this client writes it, if any past sheet shows one."""
    for m in plan.matches:
        if m.sheet.client != plan.target.client:
            continue
        for r in m.sheet.rows:
            if section_key(r.section) == key:
                return r.section
    return fallback or key.replace("_", " ").title()
