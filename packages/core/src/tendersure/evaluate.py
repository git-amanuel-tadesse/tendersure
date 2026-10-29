"""Leave-one-out evaluation against sheets the team actually built.

For each existing sheet: hide it (and any copy with the same description),
generate from its category description, and compare with the real thing.

Measures, per sheet:
  overlap      share of real questions present in the generated sheet
               (multiset match on normalised text within section)
  extra        generated questions the real sheet does not have
  flag recall  share of `extra` rows that were flagged for reading
  flag prec.   share of flagged rows that really were extra
  suggested    share of missing questions that appear among suggestions
  marks        share of scored sections whose marks match exactly

Baseline: today's process copies an arbitrary earlier sheet from the same
client. Its overlap is averaged over every other same-client sheet.
"""

from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import List, Optional

from .generate import Generator, Target
from .parser import Sheet, normalise, section_key


@dataclass
class Result:
    code: str
    description: str
    skeleton: str
    overlap: float
    baseline_overlap: float
    extra: int
    missing: int
    flagged: int
    flag_recall: Optional[float]
    flag_precision: Optional[float]
    suggested: Optional[float]
    marks_match: float


def _bag(rows) -> Counter:
    return Counter((section_key(r.section), normalise(r.criteria)) for r in rows)


def _overlap(gen: Counter, real: Counter) -> float:
    total = sum(real.values())
    return sum((gen & real).values()) / total if total else 1.0


def _section_marks(rows):
    out = Counter()
    for r in rows:
        out[section_key(r.section)] += r.marks or 0
    return out


def evaluate(gen: Generator, sheets: List[Sheet], client: Optional[str] = None) -> List[Result]:
    results = []
    targets = [s for s in sheets if s.description and s.client and (client is None or s.client == client)]
    for t in targets:
        hide = {s.name for s in sheets if normalise(s.description or "") == normalise(t.description)}
        group = gen.retriever.groups.get(t.name)
        plan = gen.plan(Target(t.code or t.name, t.description, t.client, group), exclude=hide)
        real = _bag(t.rows)
        produced = [q.row for q in plan.questions]
        g = _bag(produced)

        extra_keys = g - real
        missing = real - g
        flagged = [q for q in plan.questions if q.needs_reading]
        flagged_keys = Counter((section_key(q.row.section), normalise(q.row.criteria)) for q in flagged)
        n_extra = sum(extra_keys.values())
        flag_hits = sum((flagged_keys & extra_keys).values())
        sugg = Counter((s.section, normalise(s.row.criteria)) for s in plan.suggestions)

        others = [s for s in sheets if s.client == t.client and s.name not in hide]
        base = mean(_overlap(_bag(o.rows), real) for o in others) if others else 0.0

        rm, gm = _section_marks(t.rows), _section_marks(produced)
        scored = [k for k in set(rm) | set(gm) if rm[k] or gm[k]]
        marks = sum(1 for k in scored if abs(rm[k] - gm[k]) < 1e-6) / len(scored) if scored else 1.0

        results.append(Result(
            code=t.name, description=t.description, skeleton=plan.skeleton.sheet.name,
            overlap=_overlap(g, real), baseline_overlap=base,
            extra=n_extra, missing=sum(missing.values()), flagged=len(flagged),
            flag_recall=flag_hits / n_extra if n_extra else None,
            flag_precision=flag_hits / len(flagged) if flagged else None,
            suggested=(sum((sugg & missing).values()) / sum(missing.values())) if missing else None,
            marks_match=marks,
        ))
    return results


def summarise(results: List[Result]) -> str:
    def avg(xs):
        xs = [x for x in xs if x is not None]
        return f"{mean(xs):.0%}" if xs else "n/a"
    lines = [
        f"{'sheet':20} {'built from':20} {'overlap':>7} {'base':>6} {'extra':>5} {'flag':>4} "
        f"{'f.rec':>6} {'f.prec':>6} {'sugg':>5} {'marks':>5}",
    ]
    for r in results:
        lines.append(
            f"{r.code[:20]:20} {r.skeleton[:20]:20} {r.overlap:7.0%} {r.baseline_overlap:6.0%} "
            f"{r.extra:5d} {r.flagged:4d} "
            f"{'-' if r.flag_recall is None else f'{r.flag_recall:.0%}':>6} "
            f"{'-' if r.flag_precision is None else f'{r.flag_precision:.0%}':>6} "
            f"{'-' if r.suggested is None else f'{r.suggested:.0%}':>5} {r.marks_match:5.0%}")
    lines.append(
        f"\nmean overlap {avg(r.overlap for r in results)} (baseline, arbitrary same-client copy: "
        f"{avg(r.baseline_overlap for r in results)}); "
        f"flag recall {avg(r.flag_recall for r in results)}, precision {avg(r.flag_precision for r in results)}; "
        f"missing rows found in suggestions {avg(r.suggested for r in results)}; "
        f"section marks exact {avg(r.marks_match for r in results)}")
    return "\n".join(lines)


# --- the model's judgement against real decisions --------------------------------

def _toks(text: str) -> set:
    import re
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _similar(a: str, b: str, threshold: float = 0.5) -> bool:
    A, B = _toks(a), _toks(b)
    return bool(A and B) and len(A & B) / len(A | B) >= threshold


def _in_sheet(sheet: Sheet, text: str) -> bool:
    return any(_similar(r.criteria, text) for r in sheet.rows)


def _best(sheet: Sheet, text: str) -> float:
    """Closest wording in the sheet, 0-1 (word overlap)."""
    A = _toks(text)
    return max((len(A & _toks(r.criteria)) / len(A | _toks(r.criteria))
                for r in sheet.rows if A and _toks(r.criteria)), default=0.0)


def model_jobs(gen: Generator, sheets: List[Sheet], client: Optional[str] = None):
    """One rebuilt plan per usable sheet, with that sheet (and copies) hidden."""
    from .trust import CLEAN, CORRECTABLE
    jobs = []
    for t in sheets:
        if not (t.client and t.description) or (client and t.client != client):
            continue
        g = gen.grades.get(t.name)
        if g is not None and g.tier not in (CLEAN, CORRECTABLE):
            continue                       # a broken sheet is no ground truth
        hide = {s.name for s in sheets if normalise(s.description or "") == normalise(t.description)}
        group = gen.retriever.groups.get(t.name)
        plan = gen.plan(Target(t.code or t.name, t.description, t.client, group), exclude=hide)
        if any(q.needs_reading for q in plan.questions) or plan.suggestions:
            jobs.append((t, plan))
    return jobs


def evaluate_model(jobs, selector, cache_path: Optional[str] = None, workers: int = 3, progress=print,
                   reviewer=None, gen=None):
    """Ask the model about each rebuilt sheet and score it against the real one.

    Proposals are cached by sheet name, so an interrupted run resumes without
    paying for the same call twice.
    """
    import json
    import os
    from concurrent.futures import ThreadPoolExecutor
    from .review import qkey

    cache = {}
    if cache_path and os.path.exists(cache_path):
        with open(cache_path) as fh:
            cache = json.load(fh)

    def run(job):
        t, plan = job
        try:
            if t.name in cache:
                plan.proposal = cache[t.name]
            else:
                selector.propose(plan)
            if reviewer is not None and f"{t.name}#review" not in cache:
                from .reviewer import decisions_for
                from .review import decision_items
                decisions = decisions_for(plan, decision_items([plan]), gen)
                plan.review = {"decisions": decisions, "verdicts": reviewer.review(plan, decisions)}
            return t, plan, None
        except Exception as e:             # one failure must not sink the run
            return t, plan, str(e)[:160]

    failures, done = [], 0
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for t, plan, err in ex.map(run, jobs):
            done += 1
            if err:
                failures.append((t.name, err))
                progress(f"  [{done}/{len(jobs)}] {t.name}: FAILED - {err}")
            else:
                cache[t.name] = plan.proposal
                if getattr(plan, "review", None) is not None:
                    cache[f"{t.name}#review"] = plan.review
                progress(f"  [{done}/{len(jobs)}] {t.name}: ok")
            if cache_path:
                os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
                with open(cache_path, "w") as fh:
                    json.dump(cache, fh)

    s = Counter()
    by_client = {}
    for t, plan in jobs:
        prop = plan.proposal = cache.get(t.name)
        if not prop:
            continue
        c = by_client.setdefault(t.client, Counter())
        for q in plan.questions:
            if not q.needs_reading:
                continue
            k = qkey(section_key(q.row.section), normalise(q.row.criteria))
            kept = _in_sheet(t, q.row.criteria)
            rw = prop.get("reword", {}).get(k)
            if rw:
                # A rewording is a keep, judged by whether the team kept a
                # question there - and whether the new words are closer to theirs.
                kept = kept or _in_sheet(t, rw["text"])
                s["reworded"] += 1
                s["reword_closer"] += _best(t, rw["text"]) > _best(t, q.row.criteria)
                s["reword_match"] += _in_sheet(t, rw["text"])
            removed = k in prop["drop"]
            key = ("remove" if removed else "keep", kept)
            s[key] += 1
            c[key] += 1
        for a in prop["add"]:
            s[("add", _in_sheet(t, a["text"]))] += 1
        for sug in plan.suggestions:
            s[("all_suggestions", _in_sheet(t, sug.row.criteria))] += 1
        for d in prop["drafts"]:
            s[("draft", _in_sheet(t, d["criteria"]))] += 1
        s["judged"] += 1
        rv = cache.get(f"{t.name}#review")
        if rv is not None:
            _score_reviewed(s, c, t, plan, rv)
    return s, by_client, failures


def _score_reviewed(s: Counter, c: Counter, t: Sheet, plan, rv: dict):
    """What actually gets applied after the reviewer: removals only when both agree."""
    from .review import qkey
    verdict = {d["id"]: rv["verdicts"].get(d["id"], {}).get("verdict", "disagree") for d in rv["decisions"]}
    from .reviewer import bank_says_absent, section_key_of
    from .schema import PROTECTED_SECTIONS
    removed = {d["key"] for d in rv["decisions"]
               if section_key_of(d["key"]) not in PROTECTED_SECTIONS
               and ((d["kind"] == "remove" and verdict[d["id"]] == "agree")
                    or (d["kind"] == "reword" and verdict[d["id"]] == "remove" and bank_says_absent(d)))}
    for q in plan.questions:
        if not q.needs_reading:
            continue
        k = qkey(section_key(q.row.section), normalise(q.row.criteria))
        kept = _in_sheet(t, q.row.criteria)
        key = ("f_remove" if k in removed else "f_keep", kept)
        s[key] += 1
        c[key] += 1
    for d in rv["decisions"]:
        if d["kind"] == "add" and verdict[d["id"]] in ("agree", "edit"):
            if d.get("draft"):
                s["f_drafts"] += 1             # new by design: no past sheet to compare with
            else:
                s[("f_add", _in_sheet(t, d["text"]))] += 1
        if d["kind"] == "add" and verdict[d["id"]] == "disagree":
            s["f_add_vetoed"] += 1
        if d["kind"] == "remove" and verdict[d["id"]] != "agree":
            s["f_vetoed"] += 1
    s["reviewed"] += 1


def summarise_model(s: Counter, by_client: dict, failures: list) -> str:
    def pct(a, b):
        return f"{a}/{b} ({a / b:.0%})" if b else "n/a"
    rk, rb = s[("remove", False)], s[("remove", True)]
    kk, kb = s[("keep", True)], s[("keep", False)]
    total = rk + rb + kk + kb
    lines = [
        f"\nSheets judged: {s['judged']}" + (f" ({len(failures)} failed - rerun to retry them)" if failures else ""),
        f"\nPROPOSER ALONE - FLAGGED ROWS ({total}): should the copied question stay?",
        f"  model said remove   right {pct(rk, rk + rb)}",
        f"  model said keep     right {pct(kk, kk + kb)}",
        f"  removals caught     {pct(rk, rk + kb)} of the rows the team really removed",
        f"  overall             {pct(rk + kk, total)}   vs 'keep everything': {pct(kk + rb, total)}",
    ]
    for client, c in sorted(by_client.items()):
        n = sum(v for k, v in c.items() if isinstance(k, tuple) and k[0] in ("remove", "keep"))
        lines.append(f"    {client:6} overall {pct(c[('remove', False)] + c[('keep', True)], n)}")
    if s["reworded"]:
        lines.append(f"  reworded            {s['reworded']} rows (counted as keeps); new wording closer to the "
                     f"team's than the copied text: {pct(s['reword_closer'], s['reworded'])}, "
                     f"matching it: {pct(s['reword_match'], s['reworded'])}")
    if s["reviewed"]:
        fk, fb = s[("f_remove", False)], s[("f_remove", True)]
        gk, gb = s[("f_keep", True)], s[("f_keep", False)]
        ft = fk + fb + gk + gb
        lines += [
            f"\nAFTER AI REVIEW (what is actually applied; {s['reviewed']} sheets, {s['f_vetoed']} removals vetoed):",
            f"  removals applied    right {pct(fk, fk + fb)}",
            f"  removals caught     {pct(fk, fk + gb)} of the rows the team really removed",
            f"  overall             {pct(fk + gk, ft)}   vs 'keep everything': {pct(gk + fb, ft)}",
        ]
        for client, cc in sorted(by_client.items()):
            n = sum(v for k, v in cc.items() if isinstance(k, tuple) and k[0].startswith("f_"))
            if n:
                lines.append(f"    {client:6} overall {pct(cc[('f_remove', False)] + cc[('f_keep', True)], n)}")
        lines.append(f"  bank additions      {pct(s[('f_add', True)], s[('f_add', True)] + s[('f_add', False)])} "
                     f"of those applied were used by the team ({s['f_add_vetoed']} additions vetoed)")
        lines.append(f"  new drafted questions applied: {s['f_drafts']} ({s['f_drafts'] / max(1, s['reviewed']):.1f} per "
                     f"sheet, 0 marks each; new by design, so no past sheet can score them)")
    lines += [
        f"\nADDITIONS: model picked {pct(s[('add', True)], s[('add', True)] + s[('add', False)])} "
        f"that the team really used   vs adding every suggestion: "
        f"{pct(s[('all_suggestions', True)], s[('all_suggestions', True)] + s[('all_suggestions', False)])}",
        f"DRAFTS: {s[('draft', True)] + s[('draft', False)]} written, {s[('draft', True)]} close to a question "
        f"the team actually asked (drafts are new by design; low is not bad)",
        "\nHow to read it: a removal is 'right' if the real sheet lacks that question; a keep is right if "
        "it has it. If 'overall' is well above 'keep everything' and removals are right ~90% of the time, "
        "the model's removals can be trusted to apply automatically.",
    ]
    return "\n".join(lines)
