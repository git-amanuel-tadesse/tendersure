"""Requirement-level retrieval: which past categories are most like this one?

Match on the category description, not on individual questions, so the
generator inherits a questionnaire someone actually used rather than a pile of
individually plausible questions.

The default embedder is TF-IDF over word unigrams and bigrams: no network, no
key, deterministic, and good enough on short procurement descriptions. Any
object with `fit(texts)` and `vector(text) -> dict` can replace it (an API
embedding model, say) without touching the ranking.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .categories import category_kind
from .parser import Sheet

_STOP = {
    "of", "and", "the", "for", "to", "in", "a", "an", "etc", "e", "g", "eg", "i", "e.g",
    "including", "such", "as", "like", "with", "on", "at", "from", "both", "other",
    "related", "different", "types", "provision", "supply", "services", "service",
}


def tokens(text: str) -> List[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP]
    words = [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


class TfidfEmbedder:
    def __init__(self):
        self.idf: Dict[str, float] = {}

    def fit(self, texts: List[str]):
        df = Counter()
        for t in texts:
            df.update(set(tokens(t)))
        n = len(texts)
        self.idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
        return self

    def vector(self, text: str) -> Dict[str, float]:
        tf = Counter(tokens(text))
        default = max(self.idf.values(), default=1.0)
        v = {w: c * self.idf.get(w, default) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}


def cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


@dataclass
class Match:
    sheet: Sheet
    score: float
    text_score: float
    reasons: List[str] = field(default_factory=list)


# Ranking weights. Text similarity dominates; the boosts break ties in favour
# of the client's own conventions and the client's own grouping.
W_SAME_CLIENT = 0.25
W_SAME_GROUP = 0.30
W_GROUP_FAMILY = 0.15     # "ENGINEERING" vs "ENGINEERING (FACILITIES SECTION)"
W_SAME_KIND = 0.20


def group_family(group: Optional[str]) -> Optional[str]:
    return group.split("(")[0].strip() if group else None


class Retriever:
    def __init__(self, sheets: List[Sheet], groups: Optional[Dict[str, str]] = None,
                 embedder=None):
        """`groups` maps sheet name -> advert group, where the advert is known."""
        self.sheets = [s for s in sheets if s.description]
        self.groups = groups or {}
        self.embedder = (embedder or TfidfEmbedder()).fit([s.description for s in self.sheets])
        self.vectors = {s.name: self.embedder.vector(s.description) for s in self.sheets}

    def rank(self, description: str, client: Optional[str] = None,
             group: Optional[str] = None, exclude: Optional[set] = None,
             k: int = 5) -> List[Match]:
        q = self.embedder.vector(description)
        kind = category_kind(description)
        out = []
        for s in self.sheets:
            if exclude and s.name in exclude:
                continue
            t = cosine(q, self.vectors[s.name])
            score, why = t, [f"text {t:.2f}"]
            if client and s.client == client:
                score += W_SAME_CLIENT
                why.append("same client")
            theirs = self.groups.get(s.name)
            if group and theirs == group:
                score += W_SAME_GROUP
                why.append(f"same group ({group})")
            elif group and theirs and group_family(theirs) == group_family(group):
                score += W_GROUP_FAMILY
                why.append(f"related group ({theirs})")
            if category_kind(s.description) == kind:
                score += W_SAME_KIND
                why.append(f"same kind ({kind})")
            out.append(Match(s, score, t, why))
        out.sort(key=lambda m: -m.score)
        return out[:k]
