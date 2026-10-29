"""The reviewer's decisions: one short list, applied by code.

A batch of 58 sheets produces ~190 rows worth reading, 127 identical
warnings and hundreds of marks figures - but only a few dozen actual
decisions, because the same question was carried into many categories and
many categories were copied from the same sheet. This module collapses the
review into those decisions:

  Q-...  question carried into several categories  -> "remove: 3, 7" / "reword: 9" /
                                                        "reword 9: own wording" (combine with ';')
  A-...  questions to add to one category           -> "add all" / "add: 1, 3" / "skip"
  N-...  category with no source in the corpus      -> questions need writing
  M-...  marks copied from one source sheet         -> "technical 10, trade_reference 35"
  C-...  client's sheets disagree, no convention    -> a value; saved as a standing ruling
  T-...  team-wide rule, decided once               -> "keep" / "set flags"

The reviewer answers in the Decisions tab of the QA report and reruns
`generate --review <qa report>`; code applies the answers to every affected
sheet. IDs are stable, so answers carry over to the next run and the QA file
is the record of what was decided. A blank answer accepts the proposal.
"""

import hashlib
import re
from collections import defaultdict
from datetime import date
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openpyxl

from .generate import (MarksError, Plan, apply_section_marks, check_total, drop_empty_sections, drop_row,
                       keep_section_totals, set_field, spread_marks, to_sheet)
from .parser import normalise, section_key
from .trust import parse_value
from .validate import validate_sheet

QUESTION, MARKS, TEAM, CHECK, RULING, ADD = "question", "marks", "team", "check", "ruling", "add"
GROUNDED = 0.3      # donor description this similar to the target counts as the same topic


@dataclass
class Item:
    id: str
    type: str
    decide: str
    codes: List[str]
    proposed: str
    why: str = ""
    answer: str = ""
    keys: List[str] = field(default_factory=list)     # Q items: the questions covered
    status: str = ""
    ruling: dict = None                               # C items: the ruling to save, once applied
    payload: list = None                              # A items: the numbered additions


def number_of(code: str) -> str:
    m = re.search(r"(\d+)$", code)
    return str(int(m.group(1))) if m else code


def _short(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:6]


def qkey(section: str, normalised_text: str) -> str:
    return f"{section}|{normalised_text}"


def _marks_text(marks: Dict[str, float]) -> str:
    return ", ".join(f"{k} {v:g}" for k, v in marks.items() if v)


def decision_items(plans: List[Plan]) -> List[Item]:
    items: List[Item] = []

    # Q: flagged rows, grouped by question across categories; questions from
    # the same section that went into exactly the same categories are one
    # decision (they arrived together and nearly always go together).
    groups: Dict[Tuple[str, str], dict] = {}
    for p in plans:
        for q in p.questions:
            if not q.needs_reading:
                continue
            k = (section_key(q.row.section), normalise(q.row.criteria))
            g = groups.setdefault(k, {"text": q.row.criteria, "codes": [], "why": set()})
            if p.target.code not in g["codes"]:
                g["codes"].append(p.target.code)
            g["why"].add(q.notes[0].split(";")[0] if q.notes else "")
    by_code = {p.target.code: p for p in plans}

    def proposed_drops(sec, key, codes):
        k = qkey(sec, key)
        return tuple(c for c in codes if by_code[c].proposal and k in by_code[c].proposal["drop"])

    # Bundle only questions the model (if any) treats the same way, so one
    # answer is right for every question in the bundle.
    def rewords(sec, key, codes):
        k = qkey(sec, key)
        return {c: by_code[c].proposal["reword"][k] for c in codes
                if by_code[c].proposal and k in by_code[c].proposal.get("reword", {})}

    # Bundle only questions the model (if any) treats the same way, so one
    # answer is right for every question in the bundle. A question with
    # proposed rewordings stands alone: its new wording differs per category.
    bundles: Dict[tuple, List[Tuple[str, dict]]] = defaultdict(list)
    for (sec, key), g in groups.items():
        alone = key if rewords(sec, key, g["codes"]) else None
        bundles[(sec, tuple(g["codes"]), proposed_drops(sec, key, g["codes"]), alone)].append((key, g))
    for (sec, codes, _, _), members in sorted(bundles.items(), key=lambda kv: (-len(kv[0][1]), kv[0][0])):
        keys = sorted(qkey(sec, k) for k, _ in members)
        texts = [g["text"] for _, g in members]
        why = sorted({w for _, g in members for w in g["why"] if w})
        decide = f"[{sec}] {texts[0]}" if len(texts) == 1 else \
            f"[{sec}] {len(texts)} questions:\n" + "\n".join(f"- {t}" for t in texts)
        proposed, why_text = "keep in all", "; ".join(why)[:300]
        drop_codes, reasons, model = [], [], None
        for c in codes:
            prop = by_code[c].proposal
            hits = [prop["drop"][k] for k in keys if prop and k in prop["drop"]]
            if hits:
                drop_codes.append(c)
                reasons.append(f"{number_of(c)}: {hits[0]}")
                model = prop["model"]
        rw = rewords(sec, members[0][0], codes) if len(members) == 1 else {}
        parts = []
        if rw:
            parts.append("reword all" if len(rw) == len(codes) else
                         "reword: " + ", ".join(number_of(c) for c in rw))
            model = model or next(by_code[c].proposal["model"] for c in rw)
            decide += "\n" + "\n".join(f"{number_of(c)} -> {r['text']}" for c, r in rw.items())
            reasons = [f"{number_of(c)}: {r['reason']}" for c, r in rw.items()] + reasons
        if drop_codes:
            parts.append("remove all" if len(drop_codes) == len(codes) else
                         "remove: " + ", ".join(number_of(c) for c in drop_codes))
        if parts:
            proposed = "; ".join(parts)
            why_text = f"Proposed by {model}. " + "; ".join(reasons)[:500]
        items.append(Item(
            id=f"Q-{_short('+'.join(keys))}", type=QUESTION, decide=decide, codes=list(codes),
            proposed=proposed, why=why_text, keys=keys,
            payload=[{"code": c, "text": r["text"]} for c, r in rw.items()]))

    # A: questions to add - proposed by the model, or, without one, the best
    # suggestions for categories whose source has little in common with them.
    for p in plans:
        if p.same_category:
            continue
        entries, proposed, why_text = [], "skip", ""
        if p.proposal and (p.proposal["add"] or p.proposal["drafts"]):
            for a in p.proposal["add"]:
                entries.append(dict(a, kind="suggestion"))
            for d in p.proposal["drafts"]:
                entries.append(dict(d, kind="draft", text=d["criteria"]))
            proposed = "add all"
            why_text = f"Proposed by {p.proposal['model']}. " + "; ".join(
                f"{i}: {e['reason']}" for i, e in enumerate(entries, 1) if e.get("reason"))[:500]
        elif not p.proposal:
            # Without a model, offer only suggestions from a past sheet that
            # is on this category's topic - and closer to it than the source.
            grounded = [s for s in p.suggestions if s.text_score >= GROUNDED
                        and s.text_score > p.skeleton.text_score + 0.1]
            ranked = sorted(grounded, key=lambda s: s.section not in ("technical", "sla", "hr"))
            for s in ranked[:5]:
                entries.append({"kind": "suggestion", "source": s.source, "section": s.section, "text": s.row.criteria})
            if entries:
                donor = ranked[0].reason.split("'")[1] if "'" in ranked[0].reason else ranked[0].source
                why_text = (f"Built from {p.skeleton.sheet.name} ({p.skeleton.sheet.description}), which has "
                            f"little in common with this category. These come from '{donor}'.")
        if entries:
            lines = []
            for i, e in enumerate(entries, 1):
                tag = "NEW, drafted" if e["kind"] == "draft" else f"from {e['source']}"
                lines.append(f"{i}. [{e['section']}] {e['text']} ({tag})")
            items.append(Item(
                id=f"A-{p.target.code}", type=ADD,
                decide=f"Add to {number_of(p.target.code)} ({p.target.description[:60]}):\n" + "\n".join(lines),
                codes=[p.target.code], proposed=proposed, why=why_text, payload=entries))
    # N: categories left with only the generic template: one line for all.
    bare = [p for p in plans if p.weak_match and not p.proposal
            and not any(i.id == f"A-{p.target.code}" for i in items)]
    if bare:
        items.append(Item(
            id="N-NO-SOURCE", type=CHECK,
            decide=f"{len(bare)} categories have no past sheet on the same topic, so they carry only the "
                   f"client's generic template and no category-specific questions.",
            codes=[p.target.code for p in bare], proposed="write questions",
            why="Run with --model to have questions drafted and proposed here, or add them by hand."))

    # C: a client's sheets disagree and there is no convention or ruling yet.
    contested: Dict[Tuple[str, str, str], dict] = {}
    for p in plans:
        for q in p.questions:
            for fld, values in q.contested:
                g = contested.setdefault((q.client, q.ctx_key, fld),
                                         {"text": q.row.criteria, "codes": [], "values": values})
                if p.target.code not in g["codes"]:
                    g["codes"].append(p.target.code)
    for (client, key, fld), g in sorted(contested.items(), key=lambda kv: -sum(kv[1]["values"].values())):
        ranked = sorted(g["values"].items(), key=lambda kv: -kv[1])
        seen = ", ".join(f"{v}: {n}" for v, n in ranked)
        items.append(Item(
            id=f"C-{_short(client + '|' + key + '|' + fld)}", type=RULING,
            decide=f"{client} sheets disagree on {fld} for '{g['text']}' ({seen})",
            codes=g["codes"], proposed=f"as copied (majority: {ranked[0][0]})",
            why="Answer a value, or 'majority'. Saved as a standing ruling for this client: "
                "never asked again.",
            keys=[client, key, fld, str(ranked[0][0])]))
    n_c = sum(1 for i in items if i.type == RULING)
    if n_c >= 3:
        items.append(Item(
            id="T-ADOPT-MAJORITY", type=TEAM,
            decide=f"Settle all {n_c} client disagreements (C- lines) by majority, except those answered individually?",
            codes=sorted({c for i in items if i.type == RULING for c in i.codes}), proposed="no",
            why="Answer 'yes' to save every majority value as a standing ruling in one step."))

    # M: categories sharing a source sheet share its marks.
    by_src: Dict[Tuple[str, str], List[Plan]] = defaultdict(list)
    for p in plans:
        sk = p.skeleton.sheet
        if sk.code == p.target.code:
            continue                       # the client's own sheet for this category
        marks = {k: v[0] for k, v in p.section_marks().items()}
        by_src[(sk.name, _marks_text(marks))].append(p)
    for (src, text), ps in by_src.items():
        alts = defaultdict(set)
        for p in ps:
            for key, seen in p.marks_alternatives.items():
                mine = p.section_marks().get(key, (0,))[0]
                alts[key] |= {v for v in seen.values() if abs(v - mine) > 1e-9}
        options = "; ".join(f"{k}: {', '.join(f'{v:g}' for v in sorted(vs))} elsewhere"
                            for k, vs in alts.items() if vs)
        items.append(Item(
            id=f"M-{src}-{_short(text)}", type=MARKS,
            decide=f"Marks for categories copied from {src} ({ps[0].skeleton.sheet.description})",
            codes=[p.target.code for p in ps], proposed=text,
            why=("Similar categories used " + options) if options else "Close matches agree."))

    # T / CHECK: remaining findings, grouped by rule.
    by_rule: Dict[str, dict] = {}
    for p in plans:
        for f in p.findings:
            g = by_rule.setdefault(f.rule, {"codes": [], "n": 0, "msg": f.message})
            g["n"] += 1
            if p.target.code not in g["codes"]:
                g["codes"].append(p.target.code)
    for rule, g in by_rule.items():
        if rule == "AT-NO-UPLOAD-FLAG":
            items.append(Item(
                id="T-UPLOAD-FLAG", type=TEAM,
                decide=f"{g['n']} upload questions have neither the Attachment (L) nor the Compliance (M) flag, "
                       "as in the client's past sheets.",
                codes=g["codes"], proposed="keep",
                why="Answer 'set flags' to set M where the row carries compliance marks and L elsewhere. "
                    "Ask the platform team what an unflagged upload does if unsure."))
        else:
            items.append(Item(
                id=f"F-{rule}", type=CHECK, decide=f"{g['n']}x {rule}: {g['msg']}",
                codes=g["codes"], proposed="fix in the sheet",
                why="No automatic fix; see Detail: findings for rows."))
    return items


# --- reading answers back -------------------------------------------------------

def read_answers(path: str) -> Dict[str, str]:
    wb = openpyxl.load_workbook(path, data_only=True)
    if "Decisions" not in wb.sheetnames:
        raise ValueError(f"{path} has no Decisions tab")
    ws = wb["Decisions"]
    head = [c.value for c in ws[1]]
    i_id, i_ans = head.index("ID"), head.index("Your answer")
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[i_id] and row[i_ans] is not None and str(row[i_ans]).strip():
            out[str(row[i_id]).strip()] = str(row[i_ans]).strip()
    return out


def read_proposals(path: str) -> Dict[str, dict]:
    import json
    wb = openpyxl.load_workbook(path, data_only=True)
    if "_proposals" not in wb.sheetnames:
        return {}
    return {row[0]: json.loads(row[1]) for row in wb["_proposals"].iter_rows(min_row=2, values_only=True)
            if row[0] and row[1]}


def parse_q_answer(answer: str, codes: List[str], model_text: Dict[str, str],
                   n_questions: int) -> Tuple[set, Dict[str, str]]:
    """'remove: 3, 7; reword: 9; reword 12: own wording' -> (codes to remove,
    {code: new wording}). Raises ValueError with what to write instead."""
    by_num = {number_of(c): c for c in codes}
    removes, rewrites = set(), {}

    def nums(text):
        listed = [n.strip() for n in text.split(",") if n.strip()]
        bad = [n for n in listed if n not in by_num]
        if bad:
            raise ValueError(f"categories {', '.join(bad)} are not on this line")
        return [by_num[n] for n in listed]

    for clause in [c.strip() for c in answer.split(";") if c.strip()]:
        low = clause.lower()
        m = re.match(r"^reword\s+(\d+)\s*:\s*(.+)$", clause, re.I)
        if m:
            if n_questions != 1:
                raise ValueError("own wording works on a line with one question")
            code = nums(m.group(1))[0]
            rewrites[code] = " ".join(m.group(2).split())
        elif low in ("reword all", "reword"):
            if not model_text:
                raise ValueError("no proposed wording here; write 'reword 9: new wording'")
            rewrites.update(model_text)
        elif low.startswith("reword"):
            for code in nums(low.split(":", 1)[-1].replace("reword", "")):
                if code not in model_text:
                    raise ValueError(f"no proposed wording for {number_of(code)}; "
                                     f"write 'reword {number_of(code)}: new wording'")
                rewrites[code] = model_text[code]
        elif low in ("remove all", "remove"):
            removes.update(codes)
        elif low.startswith("remove"):
            removes.update(nums(low.split(":", 1)[-1].replace("remove", "")))
        elif low.startswith("keep:") or (low.startswith("keep ") and re.search(r"\d", low)):
            kept = set(nums(low.split(":", 1)[-1].replace("keep", "")))
            removes.update(c for c in codes if c not in kept)
        elif low in ("keep", "keep in all"):
            continue
        else:
            raise ValueError("write e.g. 'remove: 3, 7', 'reword: 9', 'reword 9: new wording' "
                             "or 'keep: 1, 2' (separate several with ';')")
    return removes, rewrites


_Q = re.compile(r"^(remove|keep)\s*(?:all|:?\s*([\d,\s]+))?$", re.I)
_PAIR = re.compile(r"([a-z_]+)\s*[:=]?\s*(\d+(?:\.\d+)?)", re.I)


def auto_reword(plans: List[Plan]):
    """Apply every reworded question the model proposed, during the build.

    Rewording keeps a question's answer type, flags and marks, so it is safe
    to apply without waiting for a reviewer; removals and additions stay as
    proposals. The QA report shows each one as 'reworded automatically', and
    answering 'keep' on that line undoes it on the next --review run.
    Returns (decision items for the QA report, number of rows reworded).
    """
    items = decision_items(plans)
    answers = {it.id: "reword: " + ", ".join(number_of(e["code"]) for e in it.payload)
               for it in items if it.type == QUESTION and it.payload}
    if not answers:
        return items, 0
    out, _, _ = apply_answers(plans, answers)
    n = 0
    for it in out:
        if it.id in answers:
            n += len(it.payload)
            it.status = "reworded automatically" + ("; removal still proposed" if "remove" in it.proposed else "")
        else:
            it.status = ""              # still the reviewer's to decide
        it.answer = ""
    return out, n


def _add_entry(p: Plan, e: dict, item_id: str, problems: List[str]) -> int:
    from copy import deepcopy
    from .generate import ADAPTED, NEW, insert_rows
    from .parser import Row
    from .selectors import label_for
    if e["kind"] == "suggestion":
        s = next((s for s in p.suggestions if s.source == e["source"] and s.row.criteria == e["text"]), None)
        if s is None:
            problems.append(f"{item_id}: suggestion {e['source']} is no longer offered for {p.target.code}")
            return 0
        insert_rows(p, s.section, [deepcopy(s.row)], ADAPTED, s.source, f"added by review {item_id}",
                    section_label=label_for(p, s.section, s.row.section))
        return 1
    opts = (e.get("options") or "").strip() if e["answer_type"] in (2, 3) else ""
    row = Row(source_file="<drafted>", sheet=p.target.code, excel_row=0, criteria=e["text"].strip(),
              marks=0.0, answer_type=e["answer_type"], options=opts or None,
              option_scores=",".join("0" for _ in opts.split(",")) if opts else None,
              attachment=e["answer_type"] == 5)
    insert_rows(p, e["section"], [row], NEW, "model draft",
                f"drafted by the model, accepted in review {item_id}: {e.get('reason', '')}",
                section_label=label_for(p, e["section"], None))
    return 1


def apply_answers(plans: List[Plan], answers: Dict[str, str],
                  ruled: Dict[str, dict] = None,
                  protect: Optional[set] = None) -> Tuple[List[Item], List[str], List[str]]:
    """Apply the reviewer's answers.

    Returns (items, log, problems). `items` are the decisions as proposed
    before the answers were applied, with the answers attached, plus any new
    decision the answers created (a section emptied by a removal) - this is
    what the next QA report shows, so answers carry forward under stable IDs.
    """
    log, problems = [], []
    by_code = {p.target.code: p for p in plans}
    items = decision_items(plans)
    known = {i.id for i in items}
    ruled = ruled or {}
    for aid in answers:
        if aid in ruled and aid not in known:
            r = ruled[aid]
            items.append(Item(id=aid, type=RULING, codes=[],
                              decide=f"{r['client']} {r['field']} for '{r['question']}'",
                              proposed="", answer=answers[aid],
                              status=f"standing ruling since {r['decided_on']}"))
            known.add(aid)
        elif aid not in known and not aid.startswith("M-FREED-"):  # FREED ids appear below
            problems.append(f"{aid}: no longer in this batch (answer ignored)")
    for it in items:
        it.answer = answers.get(it.id, "")

    def accepted(it):
        a = it.answer.strip().lower()
        return not a or a in ("keep", "keep in all", "ok", "yes", "no", it.proposed.lower())

    def effective(it):
        """Q and A lines: a blank answer accepts the proposal."""
        return it.answer.strip() or it.proposed

    # 1. Add questions first, so a section whose old questions are removed
    #    below keeps its marks on the questions that replace them.
    for it in [i for i in items if i.type == ADD]:
        eff = effective(it).lower()
        m = re.match(r"^(add all|add\s*:?\s*([\d,\s]+)|skip|none|no)$", eff)
        if not m:
            it.status = "NOT APPLIED: write 'add all', 'add: 1, 3' or 'skip'"
            problems.append(f"{it.id}: '{it.answer}' - {it.status[13:]}")
            continue
        if m.group(1) in ("skip", "none", "no"):
            it.status = "skipped" if it.answer else ""
            continue
        picks = range(1, len(it.payload) + 1) if m.group(1) == "add all" else \
            sorted({int(n) for n in (m.group(2) or "").split(",") if n.strip()})
        p = by_code[it.codes[0]]
        added, bad = 0, []
        for n in picks:
            if not 1 <= n <= len(it.payload):
                bad.append(str(n))
                continue
            added += _add_entry(p, it.payload[n - 1], it.id, problems)
        it.status = (f"added {added}" + (f"; no item {', '.join(bad)}" if bad else "")
                     + ("" if it.answer else " (proposal accepted)"))
        log.append(f"{it.id}: {added} question(s) added")

    # 1b. Reword and remove questions.
    freed: Dict[Tuple[str, float, str], List[str]] = defaultdict(list)
    for it in [i for i in items if i.type == QUESTION]:
        eff = effective(it)
        if eff.lower() in ("keep", "keep in all", "ok", "yes", "no"):
            if it.answer:
                it.status = "accepted"
            continue
        model_text = {e["code"]: e["text"] for e in (it.payload or [])}
        try:
            removes, rewrites = parse_q_answer(eff, it.codes, model_text, len(it.keys))
        except ValueError as e:
            it.status = f"NOT APPLIED: {e}"
            problems.append(f"{it.id}: '{it.answer}' - {e}")
            continue
        keys = set(it.keys)
        for code, text in rewrites.items():
            if code in removes:
                continue
            for q in by_code[code].questions:
                if q.needs_reading and qkey(section_key(q.row.section), normalise(q.row.criteria)) in keys:
                    old = q.row.criteria
                    q.row.criteria = text
                    q.note(f"reworded in review {it.id} (was: {old})")
        targets = [c for c in it.codes if c in removes]
        if protect and targets and any(k.split("|", 1)[0] in protect for k in keys):
            # Answers made by the AI never remove protected questions, by any route.
            problems.append(f"{it.id}: protected section; removal refused")
            it.status = "removal refused: protected section"
            targets = []
        for code in targets:
            p = by_code[code]
            before = {k: v[0] for k, v in p.section_marks().items()}
            idx = [i for i, r in enumerate(p.rows) if r.kind == "question" and r.needs_reading
                   and qkey(section_key(r.row.section), normalise(r.row.criteria)) in keys]
            for i in reversed(idx):
                drop_row(p, i, f"review {it.id}")
            drop_empty_sections(p)
            after = {k: v[0] for k, v in p.section_marks().items()}
            for gone in [k for k in before if k not in after and before[k]]:
                # The whole section went: its marks need a new home. Propose
                # a proportional spread and ask for confirmation.
                try:
                    new = spread_marks(p, before[gone], f"'{gone}' removed by review {it.id}")
                except MarksError as e:
                    problems.append(str(e))
                    continue
                freed[(gone, before[gone], _marks_text(new))].append(code)
                before = {k: v[0] for k, v in p.section_marks().items()}
            problems += [f"{code}: {e}" for e in keep_section_totals(p, before, f"review {it.id}")]
        done = []
        if rewrites:
            done.append(f"reworded in {len([c for c in rewrites if c not in removes])}")
        if targets:
            done.append(f"removed from {len(targets)}")
        log.append(f"{it.id}: " + (", ".join(done) or "no change"))
        refused = it.status if it.status.startswith("removal refused") else ""
        it.status = ("applied: " + ", ".join(done)) if done else (refused or "accepted")
        if refused and done:
            it.status += f"; {refused}"
        if not it.answer and done:
            it.status += " (proposal accepted)"

    # Sections emptied by removals: one decision per identical outcome.
    for (gone, marks, text), codes in freed.items():
        fid = f"M-FREED-{gone}-{_short(','.join(sorted(codes)))}"
        items.append(Item(
            id=fid, type=MARKS,
            decide=f"The '{gone}' section is now empty in these categories; its {marks:g} marks "
                   f"were spread over the other sections",
            codes=codes, proposed=text,
            why="Proportional spread, proposed by code. Marks are the client's decision: "
                "confirm, or give the sections to change.",
            answer=answers.get(fid, "")))

    # 2. Marks.
    for it in [i for i in items if i.type == MARKS and not accepted(i)]:
        split = {k.lower(): float(v) for k, v in _PAIR.findall(it.answer)}
        if not split:
            it.status = "NOT APPLIED: write e.g. 'technical 10, trade_reference 35'"
            problems.append(f"{it.id}: '{it.answer}' - {it.status[13:]}")
            continue
        failed, why = [], []
        for code in it.codes:
            p = by_code[code]
            prev = {k: v[0] for k, v in p.section_marks().items()}
            try:
                apply_section_marks(p, split, f"review {it.id}")
                check_total(p)
            except MarksError as e:
                failed.append(code)
                why.append(str(e))          # the reason itself: a total that is off, a section not there...
                problems.append(f"{it.id} on {code}: {e} - kept the previous marks")
                # A rejected answer leaves the sheet as it was, not unwritten.
                apply_section_marks(p, {k: v for k, v in prev.items() if v}, "undo rejected review answer")
        it.status = (f"NOT APPLIED on {', '.join(number_of(c) for c in failed)}: "
                     f"{'; '.join(dict.fromkeys(why))}; proposed marks kept") if failed else "applied"
        log.append(f"{it.id}: marks set on {len(it.codes)} categor{'y' if len(it.codes) == 1 else 'ies'}")

    # 3. Rulings on contested fields: applied now, saved for every future batch.
    bulk = next((i for i in items if i.id == "T-ADOPT-MAJORITY"), None)
    if bulk and bulk.answer.strip().lower() in ("yes", "y"):
        for it in items:
            if it.type == RULING and it.keys and not it.answer.strip():
                it.answer = "majority"
        bulk.status = "applied"
    for it in [i for i in items if i.type == RULING and i.keys and i.answer.strip()]:
        client, key, fld, majority = it.keys
        answer = majority if it.answer.strip().lower() == "majority" else it.answer
        try:
            value = parse_value(fld, answer)
        except ValueError as e:
            it.status = f"NOT APPLIED: {e}"
            problems.append(f"{it.id}: '{it.answer}' - {e}")
            continue
        n = 0
        for p in plans:
            for q in p.questions:
                if q.client == client and q.ctx_key == key:
                    before = getattr(q.row, fld) if fld != "upload_flag" else None
                    set_field(q.row, fld, value)
                    q.contested = [c for c in q.contested if c[0] != fld]
                    q.note(f"{fld} -> {value}: ruling {it.id}" + (f" (was {before})" if before is not None else ""))
                    n += 1
        it.ruling = {"id": it.id, "client": client, "question_key": key, "field": fld, "value": str(value),
                     "decided_on": date.today().isoformat(), "question": it.decide.split("'")[1]}
        it.status = "applied; saved as a standing ruling"
        log.append(f"{it.id}: {fld} = {value} on {n} rows; saved as a standing ruling")

    # 4. Team-wide.
    for it in [i for i in items if i.type == TEAM and not accepted(i)]:
        if it.id == "T-UPLOAD-FLAG":
            if it.answer.strip().lower() != "set flags":
                it.status = "NOT APPLIED: write 'keep' or 'set flags'"
                problems.append(f"{it.id}: '{it.answer}' - {it.status[13:]}")
                continue
            n = 0
            for code in it.codes:
                for q in by_code[code].questions:
                    r = q.row
                    if r.answer_type == 5 and not (r.attachment or r.compliance_attach):
                        r.compliance_attach = bool(r.compliance)
                        r.attachment = not r.compliance_attach
                        q.note(f"upload flag set by review {it.id}")
                        n += 1
            log.append(f"{it.id}: upload flag set on {n} rows")
            it.status = "applied"

    for it in items:
        # Applying a review means untouched lines are accepted as proposed -
        # except decisions this run created (freed marks), which nobody has seen.
        if not it.status and it.type != CHECK and (it.answer or not it.id.startswith("M-FREED-")):
            it.status = "accepted" if it.answer else "accepted as proposed"
    for p in plans:
        p.findings = validate_sheet(to_sheet(p))
    return items, log, problems
