"""Rewordings of one question (spec 04 L1).

"Does your company have the necessary technology..." and "Does your
organization have the necessary technology..." are one question asked two
ways: they should share one name and one Yes/No verdict. Questions whose
normalised text is at least THRESHOLD similar form a group.

Grouping is greedy and near-linear: each question is compared only with
groups it shares a distinctive word with, so years of history stay fast.
Two questions the caller says to keep apart (both confirmed separately by the
team) are never grouped.
"""

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Callable, Dict, Iterable, Optional

THRESHOLD = 0.85
_CANDIDATES = 25        # groups compared per question, those sharing most words
_COMMON = {"a", "an", "and", "any", "are", "as", "at", "attach", "by", "client", "company", "do", "does",
           "for", "from", "has", "have", "if", "in", "is", "its", "no", "of", "on", "or", "organization",
           "organisation", "please", "the", "to", "with", "yes", "you", "your"}


def _words(key: str):
    return {w for w in key.replace(">>", " ").split() if len(w) > 2 and w not in _COMMON}


def group(keys: Iterable[str], threshold: float = THRESHOLD,
          apart: Optional[Callable[[str, str], bool]] = None) -> Dict[str, str]:
    """Each key -> the first key of its group. Keys come in priority order:
    the first of a group (say, the most used) represents it."""
    reps, out = [], {}
    index = defaultdict(set)            # distinctive word -> groups using it
    for key in keys:
        if key in out:
            continue
        words = _words(key)
        shared = Counter(i for w in words for i in index[w])
        home = None
        for i, _ in shared.most_common(_CANDIDATES):
            rep = reps[i]
            if apart and apart(key, rep):
                continue
            m = SequenceMatcher(None, key, rep)
            if m.quick_ratio() >= threshold and m.ratio() >= threshold:
                home = i
                break
        if home is None:
            home = len(reps)
            reps.append(key)
            for w in words:
                index[w].add(home)
        out[key] = reps[home]
    return out
