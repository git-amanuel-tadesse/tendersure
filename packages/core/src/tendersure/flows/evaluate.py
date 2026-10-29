"""How good is it: leave-one-out against the sheets the team built, the
model's judgement scored the same way, and what a batch would send to a model."""

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .. import categories as cat
from .. import trust
from ..generate import Generator, Target
from ..paths import current
from ..similarity import Retriever
from .common import groups_for, load_source, log


def evaluate(source: Optional[str] = None, bank: Optional[str] = None, client: Optional[str] = None,
             categories: Optional[str] = None) -> str:
    """Leave-one-out: rebuild each past sheet from the others; returns the summary."""
    from ..evaluate import evaluate as leave_one_out, summarise
    sheets, _ = load_source(source, bank or current().bank)
    groups = groups_for(sheets, cat.load(categories), client) if categories else {}
    gen = Generator(sheets, Retriever(sheets, groups))
    summary = summarise(leave_one_out(gen, sheets, client))
    log.info(summary)
    return summary


@dataclass
class ModelEvaluation:
    jobs: int
    calls: int                              # model calls to make (cached answers are not asked again)
    cache: str
    summary: Optional[str] = None           # None on a dry run


def evaluate_model(source: Optional[str] = None, bank: Optional[str] = None, client: Optional[str] = None,
                   categories: Optional[str] = None, model: Optional[str] = None, limit: Optional[int] = None,
                   workers: int = 3, cache: Optional[str] = None, dry_run: bool = False,
                   review: bool = True) -> ModelEvaluation:
    """Score the model's judgement against sheets the team built (costs model calls)."""
    from ..evaluate import evaluate_model as run, model_jobs, summarise_model
    sheets, _ = load_source(source, bank or current().bank)
    groups = groups_for(sheets, cat.load(categories), client or "ERCS") if categories else {}
    gen = Generator(sheets, Retriever(sheets, groups), grades=trust.grade(sheets))
    jobs = model_jobs(gen, sheets, client)
    if limit:
        jobs = jobs[:limit]
    from ..llm import make_llm
    llm = make_llm(model=model)          # fails here, before any cost, if the key or tier is missing
    from ..selectors import PROMPT_VERSION
    cache = cache or os.path.join(current().cache, f"model_eval_{llm.model.replace('/', '_')}_v{PROMPT_VERSION}.json")
    cached = reviewed = 0
    if os.path.exists(cache):
        with open(cache) as fh:
            seen = json.load(fh)
        names = {t.name for t, _ in jobs}
        cached = len(set(seen) & names)
        reviewed = len({k[:-7] for k in seen if k.endswith("#review")} & names)
    calls = (len(jobs) - cached) + (len(jobs) - reviewed if review else 0)
    log.info(f"{len(jobs)} rebuilt sheets to judge with {llm.provider}/{llm.model}"
             f"{' (proposer + reviewer)' if review else ''}: {calls} model call(s) to make "
             f"(about ${calls * 0.03:.2f} at $2/$10 per million tokens)")
    log.info("every request is anonymised; exact text sent is logged in .cache/llm_outbox/")
    r = ModelEvaluation(len(jobs), calls, cache)
    if dry_run:
        log.info("dry run: nothing sent")
        return r
    from ..selectors import ModelSelector
    reviewer = None
    if review:
        from ..reviewer import ModelReviewer
        reviewer = ModelReviewer()
    s, by_client, failures = run(jobs, ModelSelector(llm=llm), cache, workers, reviewer=reviewer, gen=gen)
    r.summary = summarise_model(s, by_client, failures)
    log.info(r.summary)
    log.info(f"\nproposals saved in {cache} (delete it to start over)")
    return r


@dataclass
class PrivacyCheck:
    checked: int
    replaced: Dict[str, int]                     # placeholder -> times used
    leaking: List[Tuple[str, List[str]]]         # (request, what would identify) - such requests are refused
    preview: str                                 # the folder holding the exact text that would be sent
    sent: int = field(default=0)                 # always 0: nothing is sent


def privacy_check(categories: str, client: str, prefix: str, source: Optional[str] = None,
                  bank: Optional[str] = None, client_name: Optional[str] = None, only: Optional[List[int]] = None,
                  notice: Optional[str] = None) -> PrivacyCheck:
    """Build every model request for a batch, anonymise it, scan it - send nothing."""
    from ..privacy import Anonymiser
    from ..selectors import SYSTEM, build_prompt
    sheets, _ = load_source(source, bank or current().bank)
    cats = cat.load(categories)
    if only:
        cats = [c for c in cats if c.number in set(only)]
    gen = Generator(sheets, Retriever(sheets, groups_for(sheets, cats, client)), grades=trust.grade(sheets))
    out = os.path.join(current().outbox, "preview")
    os.makedirs(out, exist_ok=True)
    checked, leaking, replaced = 0, [], Counter()
    for c in cats:
        plan = gen.plan(Target(c.code(prefix), c.description, client, c.group, client_name, c.sub_categories))
        if plan.same_category:
            continue
        anon = Anonymiser(client)
        sent = anon.anonymise(SYSTEM + "\n\n" + build_prompt(plan))
        replaced.update(t for t in re.findall(r"\[[A-Z]+(?:-[A-Z0-9]+)?\]", sent) if t != "[FLAGGED]")
        checked += 1
        if anon.leaks(sent):
            leaking.append((plan.target.code, anon.leaks(sent)))
        with open(os.path.join(out, f"{plan.target.code}.txt"), "w") as fh:
            fh.write(sent)
    if notice:
        from ..category_llm import notice_text
        anon = Anonymiser(client)
        sent = anon.anonymise(notice_text(notice))
        replaced.update(t for t in re.findall(r"\[[A-Z]+(?:-[A-Z0-9]+)?\]", sent) if t != "[FLAGGED]")
        checked += 1
        if anon.leaks(sent):
            leaking.append(("notice", anon.leaks(sent)))
        with open(os.path.join(out, "notice.txt"), "w") as fh:
            fh.write(sent)
    log.info(f"checked {checked} requests; nothing was sent")
    log.info("replaced: " + ", ".join(f"{k} x{n}" for k, n in sorted(replaced.items(), key=lambda kv: -kv[1])))
    if leaking:
        log.info(f"WOULD BE REFUSED ({len(leaking)}):")
        for code, problems in leaking:
            log.info(f"  {code}: {', '.join(problems[:4])}")
    else:
        log.info("no identifying text left in any request")
    log.info(f"exact text that would be sent: {out}/")
    return PrivacyCheck(checked, dict(replaced), leaking, out)
