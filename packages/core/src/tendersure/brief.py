"""The client's brief (spec 07 B): one text per run in which the client says
what it is looking for. The model steps that shape this client's output read
it first - the refine proposal, the generation proposer, the reviewer and the
AI check - and it ranks above past questionnaires and other clients'
practice. The template rules still run after them and win any conflict.

Learned facts shared by every client (names, Yes/No verdicts, research)
never see it: a brief would make them client-specific.
"""

import os
from typing import List, Optional

HEAD = ("THE CLIENT'S BRIEF - what the client says it is looking for. It comes before everything below: follow it "
        "over past questionnaires and other clients' practice. The agreed template rules still apply after you "
        "(the team sets marks and every sheet totals 100; Yes/No scoring; the Eligibility section holds the Trade "
        "License and documents the client states; names and acronyms). Where the brief asks for something those "
        "rules forbid, say so instead of doing it.")
FROM_BRIEF = "from the client's brief"
NOT_APPLIED = "client's brief not applied: no model"


def load(value: Optional[str]) -> str:
    """The brief as given: a text file's contents, or the text itself."""
    if not value:
        return ""
    if len(value) < 512 and os.path.isfile(value):
        with open(value, encoding="utf-8") as fh:
            return fh.read().strip()
    return value.strip()


def prompt_lines(brief: Optional[str]) -> List[str]:
    brief = (brief or "").strip()
    return [HEAD, brief, ""] if brief else []


def labelled(reason: str, from_brief) -> str:
    """A model's reason, marked when the brief led to it."""
    reason = (reason or "").strip()
    return f"{FROM_BRIEF}: {reason}" if from_brief and not reason.startswith(FROM_BRIEF) else reason
