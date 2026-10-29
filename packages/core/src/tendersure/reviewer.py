"""The AI reviewer: answers the QA decisions itself, with evidence.

The proposer (selectors.py) suggests rewordings, removals and additions. On
its own its removals were right about 80% of the time - not enough to apply
unseen. So a second, independent pass reviews every proposal for a category,
with evidence from the bank the proposer did not have:

  for a removal   how many past sheets on similar topics ask that question
  for an addition whether it came from a past sheet on the same topic, or
                  was newly drafted

and returns agree / disagree / edit for each. Code then applies only what
both passes agree on:

  removal     applied only if the reviewer agrees; otherwise kept
  rewording   applied if the reviewer agrees; an edit replaces it; "remove
              instead" is applied only if the bank agrees too (similar past
              sheets don't ask it) - a removal always needs two signals
  addition    added only if the reviewer agrees; an edited draft uses the edit

Disagreements fall back to the client's own past sheet and are recorded, so
the QA report is an audit trail, not a to-do list. Marks are never touched.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import brief as cb
from .evaluate import _in_sheet
from .generate import Generator, Plan, ai_cache_key
from .llm import make_llm
from .parser import normalise, section_key
from .review import ADD, QUESTION, Item, decision_items, number_of, qkey
from .schema import PROTECTED_SECTIONS


def section_key_of(question_key: str) -> str:
    """The section part of a review question key ('section|text')."""
    return question_key.split("|", 1)[0]

SYSTEM = """You review a supplier prequalification questionnaire that was adapted from a past category to a new one. Another assistant proposed changes. For each numbered decision, give your verdict:

- agree: the change is right for the new category
- disagree: the change is wrong; the question should stay as it is (for a removal or rewording), or should not be added (for an addition)
- edit: the change is right in spirit but the wording should be different; give the corrected question text
- remove (REWORD decisions only): rewording cannot save it; the question's whole purpose does not apply to this category

Judge each on its own. Use the evidence given: if past questionnaires on similar topics ask a question, removing it is probably wrong; if none do, removing it is probably right. Added questions must test something this category genuinely needs. Rewordings must keep the question's meaning and only change what ties it to the old category; a question no past questionnaire on a similar topic asks is often better removed than reworded. When the client's brief is given, a change it asks for is right unless it breaks a rule it lists. Keep each reason to one short sentence."""

SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["agree", "disagree", "edit", "remove"]},
            "text": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["id", "verdict", "text", "reason"],
        "additionalProperties": False}}},
    "required": ["verdicts"],
    "additionalProperties": False,
}

NEIGHBOUR_MIN_TEXT = 0.25


def removal_evidence(gen: Generator, plan: Plan, text: str) -> Tuple[str, int, int]:
    """How many past sheets on similar topics (other than the source) ask this:
    (sentence for the reviewer, count asking it, count compared)."""
    near = [m for m in gen.retriever.rank(plan.target.description, k=40)
            if m.text_score >= NEIGHBOUR_MIN_TEXT and m.sheet.name != plan.skeleton.sheet.name
            and m.sheet.code != plan.target.code and gen._usable(m)][:6]
    if not near:
        return "no past questionnaires on closely similar topics to compare with", 0, 0
    n = sum(_in_sheet(m.sheet, text) for m in near)
    return f"asked in {n} of {len(near)} past questionnaires on similar topics", n, len(near)


def bank_says_absent(d: dict) -> bool:
    """The bank's own vote for removal: at least two similar past sheets, none asking it."""
    return d.get("compared", 0) >= 2 and d.get("asked", 0) == 0


def decisions_for(plan: Plan, items: List[Item], gen: Generator) -> List[dict]:
    """The proposals touching one category, numbered for the reviewer."""
    code, out = plan.target.code, []
    by_key = {qkey(section_key(q.row.section), normalise(q.row.criteria)): q.row.criteria
              for q in plan.questions if q.needs_reading}
    prop = plan.proposal or {}
    for k, reason in prop.get("drop", {}).items():
        if k in by_key:
            ev, n, tot = removal_evidence(gen, plan, by_key[k])
            out.append({"kind": "remove", "key": k, "text": by_key[k], "reason": reason,
                        "evidence": ev, "asked": n, "compared": tot})
    for k, rw in prop.get("reword", {}).items():
        if k in by_key:
            ev, n, tot = removal_evidence(gen, plan, by_key[k])
            out.append({"kind": "reword", "key": k, "text": by_key[k], "new": rw["text"],
                        "reason": rw["reason"], "evidence": ev, "asked": n, "compared": tot})
    a_item = next((i for i in items if i.type == ADD and i.codes == [code]), None)
    if a_item and plan.proposal:
        for n, e in enumerate(a_item.payload or [], 1):
            origin = "newly drafted" if e["kind"] == "draft" else "from a past questionnaire on a similar topic"
            out.append({"kind": "add", "n": n, "text": e["text"], "section": e["section"],
                        "reason": e.get("reason", ""), "origin": origin, "draft": e["kind"] == "draft"})
    for i, d in enumerate(out, 1):
        d["id"] = f"D{i}"
    return out


def build_prompt(plan: Plan, decisions: List[dict]) -> str:
    t = plan.target
    lines = cb.prompt_lines(t.brief) + [f"NEW CATEGORY: {t.description}",
                                        f"Group in the client's notice: {t.group or 'unknown'}"]
    if t.requirements:
        lines.append(f"The client's requirements for this category: {t.requirements}")
    lines += [f"Adapted from the past category: {plan.skeleton.sheet.description}", "", "DECISIONS:"]
    for d in decisions:
        if d["kind"] == "remove":
            lines.append(f"{d['id']} REMOVE the question: \"{d['text']}\"\n     proposer's reason: {d['reason']}"
                         f"\n     evidence: {d['evidence']}")
        elif d["kind"] == "reword":
            lines.append(f"{d['id']} REWORD \"{d['text']}\"\n     to \"{d['new']}\"\n     proposer's reason: {d['reason']}"
                         f"\n     evidence for the question itself: {d['evidence']}")
        else:
            lines.append(f"{d['id']} ADD to {d['section']}: \"{d['text']}\" ({d['origin']})"
                         f"\n     proposer's reason: {d['reason']}")
    return "\n".join(lines)


class ModelReviewer:
    """Any configured model (see llm.py); TENDERSURE_REVIEW_MODEL picks a different one."""

    def __init__(self, llm=None, model: Optional[str] = None):
        import os
        self.llm = llm or make_llm(model=model or os.environ.get("TENDERSURE_REVIEW_MODEL") or None)

    def review(self, plan: Plan, decisions: List[dict]) -> Dict[str, dict]:
        if not decisions:
            return {}
        raw = self.llm.complete_json(SYSTEM, build_prompt(plan, decisions), SCHEMA, "sheet_review",
                                     client=plan.target.client, max_tokens=12000)
        known = {d["id"] for d in decisions}
        return {v["id"]: v for v in raw.get("verdicts", []) if v.get("id") in known}


def answers_from(plans: List[Plan], items: List[Item], reviews: Dict[str, Tuple[List[dict], Dict[str, dict]]]):
    """Turn both passes into Decisions-tab answers; returns (answers, notes per item)."""
    answers: Dict[str, str] = {}
    notes: Dict[str, List[str]] = defaultdict(list)
    q_actions = defaultdict(lambda: {"remove": [], "reword": {}})
    for code, (decisions, verdicts) in reviews.items():
        for d in decisions:
            v = verdicts.get(d["id"], {"verdict": "disagree", "reason": "no verdict given"})
            verdict, why = v["verdict"], v.get("reason", "")
            if d["kind"] in ("remove", "reword"):
                item = next((i for i in items if i.type == QUESTION and d["key"] in i.keys
                             and code in i.codes), None)
                if item is None:
                    continue
                act = q_actions[item.id]
                if d["kind"] == "remove":
                    if verdict == "agree":
                        act["remove"].append(code)
                    else:
                        notes[item.id].append(f"{number_of(code)}: removal not applied, reviewer: {why}")
                else:
                    if verdict == "remove" and section_key_of(d["key"]) in PROTECTED_SECTIONS:
                        notes[item.id].append(f"{number_of(code)}: reviewer suggested removing, but "
                                              f"{section_key_of(d['key'])} questions are protected; kept")
                    elif verdict == "remove" and bank_says_absent(d):
                        # Reviewer and the bank's evidence agree: remove rather than reword.
                        act["remove"].append(code)
                        notes[item.id].append(f"{number_of(code)}: removed instead of reworded - reviewer "
                                              f"and bank agree ({d['evidence']}): {why}")
                    elif verdict == "remove":
                        notes[item.id].append(f"{number_of(code)}: reviewer suggested removing, but the bank "
                                              f"does not confirm ({d['evidence']}); kept as the client had it")
                    elif verdict == "agree":
                        act["reword"][code] = None
                    elif verdict == "edit" and v.get("text", "").strip():
                        act["reword"][code] = v["text"].strip()
                        notes[item.id].append(f"{number_of(code)}: wording edited by reviewer: {why}")
                    else:
                        notes[item.id].append(f"{number_of(code)}: rewording not applied, reviewer: {why}")
            else:
                item = next((i for i in items if i.type == ADD and i.codes == [code]), None)
                if item is None:
                    continue
                key = item.id
                answers.setdefault(key, "")
                if verdict in ("agree", "edit"):
                    answers[key] = (answers[key] + f",{d['n']}") if answers[key] else str(d["n"])
                    if verdict == "edit" and d["draft"] and v.get("text", "").strip():
                        # Edit the proposal itself: the review step rebuilds its items from it.
                        plan = next(pl for pl in plans if pl.target.code == code)
                        for draft in plan.proposal["drafts"]:
                            if draft["criteria"] == d["text"]:
                                draft["criteria"] = v["text"].strip()
                        notes[key].append(f"{d['n']}: wording edited by reviewer: {why}")
                else:
                    notes[key].append(f"{d['n']}: not added, reviewer: {why}")
    for item_id, act in q_actions.items():
        parts = []
        if act["remove"]:
            parts.append("remove: " + ", ".join(number_of(c) for c in act["remove"]))
        own = {c: t for c, t in act["reword"].items() if t}
        plain = [c for c, t in act["reword"].items() if not t]
        if plain:
            parts.append("reword: " + ", ".join(number_of(c) for c in plain))
        parts += [f"reword {number_of(c)}: {t}" for c, t in own.items()]
        answers[item_id] = "; ".join(parts) or "keep in all"
    for item in items:
        if item.type == ADD:
            got = answers.get(item.id, "")
            # Nothing is added without the reviewer's agreement (a blank would accept).
            answers[item.id] = f"add: {got}" if got else "skip"
        elif item.type == QUESTION and item.id not in answers and item.proposed != "keep in all":
            answers[item.id] = "keep in all"      # proposed, but never agreed: keep as the client had it
    return answers, notes


def ai_review(gen: Generator, plans: List[Plan], reviewer: ModelReviewer, progress=print,
              cache: Optional[dict] = None, save=None):
    """Review every category's proposals and answer the Decisions tab.

    `cache`/`save` keep each verdict as soon as it arrives, so a rerun after
    a crash does not pay for the same review twice.
    Returns (items with AI answers and statuses, summary counts)."""
    from .generate import MarksError, rebalance_to_100
    from .review import apply_answers
    items = decision_items(plans)
    reviews = {}
    for p in plans:
        if not p.proposal:
            continue
        decisions = decisions_for(p, items, gen)
        key = ai_cache_key(p, build_prompt(p, decisions), "#review")
        try:
            if cache is not None and key in cache:
                verdicts = cache[key]
            else:
                verdicts = reviewer.review(p, decisions)
                if cache is not None:
                    cache[key] = verdicts
                    if save:
                        save()
        except Exception as e:                 # a failed review keeps that category conservative
            progress(f"{p.target.code}: review failed ({str(e)[:120]}); nothing applied for it")
            verdicts = {}
        reviews[p.target.code] = (decisions, verdicts)
        agree = sum(1 for v in verdicts.values() if v["verdict"] == "agree")
        progress(f"{p.target.code}: reviewer agrees with {agree} of {len(decisions)} proposal(s)")
    answers, notes = answers_from(plans, items, reviews)
    if any(i.id == "T-ADOPT-MAJORITY" for i in items):
        answers["T-ADOPT-MAJORITY"] = "yes"     # client disagreements: the client's own majority
    out, log, problems = apply_answers(plans, answers, protect=PROTECTED_SECTIONS)
    for it in out:
        if it.id in answers:
            it.answer = answers[it.id]
            it.status = f"AI: {it.status}" + (f" | {'; '.join(notes[it.id])}" if notes.get(it.id) else "")
        elif it.id.startswith("M-BENCH-"):
            it.status = ""                              # the client's benchmark split: the team's call, left open
        elif it.type == "marks" and not it.id.startswith("M-FREED-"):
            it.status = "AI: kept the client's marks (marks are the client's decision)"
        elif it.type == "marks":
            it.status = "AI: accepted the proportional spread of the emptied section's marks"
        elif it.id == "T-UPLOAD-FLAG":
            it.status = "AI: kept the client's practice for upload flags"
        elif it.type == "check":
            it.status = f"AI: noted - {it.proposed}"
        elif not it.status:
            it.status = "AI: kept as proposed"
    rebalanced = []
    done = {line.split(": ", 1)[0]: line.split(": ", 1)[1] for line in log if ": marks rebalanced to" in line}
    for p in plans:
        try:
            note = rebalance_to_100(p, "AI removals") or done.get(p.target.code)
        except MarksError as e:
            problems.append(str(e))
            continue
        if note:
            rebalanced.append(p.target.code)
            progress(f"{p.target.code}: {note}")
            for it in out:
                if it.type == "marks" and p.target.code in it.codes and not it.id.startswith("M-BENCH-"):
                    it.status = f"AI: {note}"
    disagreements = sum(len(v) for v in notes.values())
    return out, {"answered": len(answers), "disagreements": disagreements, "problems": problems,
                 "rebalanced": rebalanced}
