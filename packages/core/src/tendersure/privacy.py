"""Anonymisation for everything sent to a model. Fails closed.

Client criteria are confidential, and the corpus mixes several clients. No
model call may reveal who the clients are, whose sheet a question came from,
or any contact detail. Every request passes through `Anonymiser` inside the
model client (llm.py), so no caller can forget it:

  client names and aliases  -> [CLIENT] for the client being worked on,
                               [CLIENT-B], [CLIENT-C]... for the others
  the platform's name       -> [PLATFORM]
  sheet codes (ILRI-ET-078) -> [REF-1], [REF-2]...
  emails, URLs, phone and
  long numbers              -> [EMAIL] [URL] [PHONE] [NUMBER]

Placeholders in the model's answer are mapped back locally, so a drafted
question reads "...current supplier of ERCS?" again on this machine only.

Before anything is sent, `leaks()` scans the final request; if any known name,
code or contact pattern survives, the request is refused. Raw PDFs are never
sent: they cannot be scrubbed reliably, so callers send extracted,
anonymised text instead.

Extra names to hide (people, other organisations) go one per line in
`data/privacy_terms.txt`.
"""

import os
import re
from typing import Dict, Iterable, List, Optional

from .paths import home

# Known clients and how they are written in sheets and notices. Longest
# aliases first, so "Ethiopian Red Cross Society" is replaced before "Red Cross".
CLIENT_ALIASES: Dict[str, List[str]] = {
    "ERCS": ["Ethiopian Red Cross Society", "Ethiopian Red Cross", "Red Cross Society", "ERCS"],
    "SNV": ["SNV Netherlands Development Organisation", "SNV Netherlands", "SNV"],
    "ILRI": ["International Livestock Research Institute", "ILRI"],
    "NRC": ["Norwegian Refugee Council", "NRC"],
    "TASL": ["TASL"],
    "Schindler": ["Schindler"],
}
PLATFORM_ALIASES = ["TenderSure", "Tender Sure", "tendersure.africa"]

# Every client code seen in the corpus is registered when it is loaded, so a
# client nobody listed above is still hidden.
_REGISTERED: set = set()


def register_clients(codes: Iterable[str]):
    _REGISTERED.update(c for c in codes if c)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w-]+\.(?:africa|com|org|net|et|io)\b(?:/\S*)?", re.I)
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")    # counted as a phone only with 9+ digits
_LONGNUM = re.compile(r"\b\d{6,}\b")
# Sheet codes always carry a number (ERCS-ET-2026-002, ILRI-ET-078); requiring
# a digit keeps headings like SUB-CATEGORY and words like FIATA-affiliated.
# Codes are written with hyphens (ERCS-ET-2026-002) or underscores (NRC_2027_28_030).
_CODE = re.compile(r"\b[A-Z]{2,6}[-_](?:[A-Z]{2}[-_])?[\w]*\d[\w]*(?:[-_][\w]+)*\b")
# A name is found when no letter or digit touches it: "NRC" inside "NRC_2027"
# counts (an underscore is a word character, so \b would miss it).
_BEFORE, _AFTER = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_PLACEHOLDER = re.compile(r"\[(?:CLIENT(?:-[A-Z]{1,2})?|PLATFORM|REF-\d+|EMAIL|URL|PHONE|NUMBER|NAME-\d+)\]")


def _is_phone(s: str) -> bool:
    return sum(ch.isdigit() for ch in s) >= 9


def _extra_terms(path: str = None) -> List[str]:
    path = path or os.path.join(home(), "data", "privacy_terms.txt")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [l.strip() for l in fh if l.strip() and not l.startswith("#")]


class PrivacyError(RuntimeError):
    pass


class Anonymiser:
    def __init__(self, target_client: Optional[str] = None, extra_terms: Iterable[str] = None):
        self.forward: Dict[str, str] = {}      # real -> placeholder
        self.back: Dict[str, str] = {}         # placeholder -> real (local only)
        aliases = {c: list(a) for c, a in CLIENT_ALIASES.items()}
        for c in _REGISTERED | ({target_client} if target_client else set()):
            aliases.setdefault(c, [c])
        letters = iter(f"{a}{b}".strip() for b in " ABCDEFGHIJKLMNOPQRSTUVWXYZ" for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        next(letters)                                       # "A" is the target client
        for c in sorted(aliases, key=lambda c: (c != target_client, c)):
            ph = "[CLIENT]" if c == target_client else f"[CLIENT-{next(letters)}]"
            for alias in aliases[c]:
                self.forward[alias] = ph
            self.back[ph] = c
        for alias in PLATFORM_ALIASES:
            self.forward[alias] = "[PLATFORM]"
        self.back["[PLATFORM]"] = PLATFORM_ALIASES[0]
        for i, term in enumerate(list(extra_terms or []) + _extra_terms(), 1):
            self.forward.setdefault(term, f"[NAME-{i}]")
            self.back.setdefault(f"[NAME-{i}]", term)
        self._terms = sorted(self.forward, key=len, reverse=True)
        self._refs: Dict[str, str] = {}

    def _ref(self, code: str) -> str:
        if code not in self._refs:
            ph = f"[REF-{len(self._refs) + 1}]"
            self._refs[code] = ph
            self.back[ph] = code
        return self._refs[code]

    def anonymise(self, text: str) -> str:
        if not text:
            return text
        text = _EMAIL.sub("[EMAIL]", text)
        text = _URL.sub("[URL]", text)
        text = _CODE.sub(lambda m: self._ref(m.group(0)), text)
        for term in self._terms:
            text = re.sub(rf"{_BEFORE}{re.escape(term)}{_AFTER}", self.forward[term], text, flags=re.I)
        text = _PHONE.sub(lambda m: "[PHONE]" if _is_phone(m.group(0)) else m.group(0), text)
        text = _LONGNUM.sub("[NUMBER]", text)
        return text

    def restore(self, value):
        """Map placeholders in a model answer back, recursively through JSON."""
        if isinstance(value, str):
            return _PLACEHOLDER.sub(lambda m: self.back.get(m.group(0), m.group(0)), value)
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore(v) for k, v in value.items()}
        return value

    def leaks(self, text: str) -> List[str]:
        """Anything identifying still present in text that is about to be sent."""
        found = []
        for term in self._terms:
            if re.search(rf"{_BEFORE}{re.escape(term)}{_AFTER}", text, flags=re.I):
                found.append(f"name '{term}'")
        stripped = _PLACEHOLDER.sub("", text)
        for label, rx in (("email", _EMAIL), ("url", _URL), ("sheet code", _CODE)):
            m = rx.search(stripped)
            if m:
                found.append(f"{label} '{m.group(0)[:30]}'")
        phone = next((m.group(0) for m in _PHONE.finditer(stripped) if _is_phone(m.group(0))), None)
        if phone:
            found.append(f"phone '{phone[:30]}'")
        return found

    def guard(self, *texts: str):
        problems = sorted({p for t in texts for p in self.leaks(t or "")})
        if problems:
            raise PrivacyError("refused to send: the request still contains " + ", ".join(problems[:6]))
