"""What every flow shares: where the sheets come from, which model runs, the
learned files, and how a flow reports.

A flow reports through the `tendersure` logger (`log.info` for each line a
person should see) and returns a result; it never prints and never exits.
The CLI prints those lines; the API keeps them as a job's progress.
"""

import csv
import glob
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .. import gating
from .. import glossary as gl
from .. import research as rs
from .. import trust
from ..categories import Category
from ..parser import Sheet, parse_workbook
from ..paths import current

log = logging.getLogger("tendersure")


class FlowError(Exception):
    """A flow cannot go on: a missing input, an empty bank, nothing to write."""


@dataclass(frozen=True)
class Model:
    """Which model a flow may use.

    `id` None: not chosen, so the AI runs whenever one is configured in .env;
    "" chosen, with the configured default; otherwise a model id. `off`
    turns every model step off."""
    id: Optional[str] = None
    off: bool = False

    @property
    def name(self) -> Optional[str]:
        """The id to hand a model client (None: its default)."""
        return self.id or None

    def wanted(self) -> Tuple[bool, str]:
        """(runs, why not). The AI runs by default whenever a model is configured."""
        if self.off:
            return False, "--no-model given"
        if self.id is not None:
            return True, ""
        from ..llm import load_dotenv
        load_dotenv()
        provider = os.environ.get("TENDERSURE_PROVIDER", "openrouter").lower()
        if provider == "gemini":
            if not os.environ.get("GEMINI_API_KEY"):
                return False, "no GEMINI_API_KEY in .env"
            if os.environ.get("GEMINI_PAID_TIER", "").lower() != "yes":
                return False, "GEMINI_PAID_TIER is not 'yes' (unpaid keys may expose prompts)"
            return True, ""
        if not os.environ.get("OPENROUTER_API_KEY"):
            return False, "no model key in .env (see .env.example)"
        return True, ""


# --- where the sheets come from ------------------------------------------------

def load_corpus(folder: str, cache: bool = None) -> List[Sheet]:
    """Every workbook under `folder` (subfolders included, e.g. one per year).

    Parsed sheets are cached per file and reused until the file changes, so a
    corpus of years only costs a full parse once.
    """
    if cache is None:
        cache = os.environ.get("TENDERSURE_NO_CACHE") != "1"
    files = sorted(f for f in glob.glob(os.path.join(folder, "**", "*.xlsx"), recursive=True)
                   if not os.path.basename(f).startswith("~$"))
    if not os.path.isdir(folder):
        raise FlowError(f"corpus folder not found: {folder}\n  quote the path if it contains spaces")
    if not files:
        raise FlowError(f"no .xlsx files in {folder}")
    sheets = []
    for f in files:
        sheets.extend(_parse_cached(f) if cache else parse_workbook(f))
    from ..privacy import register_clients
    register_clients(s.client for s in sheets)     # every client is anonymised in model calls
    names = Counter(s.name for s in sheets)
    for s in sheets:                       # the same tab name in two workbooks must stay distinct
        if names[s.name] > 1:
            s.name = f"{s.name} [{os.path.splitext(s.source_file)[0]}]"
    return sheets


def load_source(source: Optional[str], bank: str) -> Tuple[List[Sheet], Optional[Callable]]:
    """Sheets for a flow: from the bank (default), or straight from a folder.

    Returns (sheets, template_loader for the writer)."""
    from ..store import BankStore
    if source and os.path.isdir(source):
        return load_corpus(source), None
    path = source or bank
    if not os.path.exists(path):
        raise FlowError(f"no bank at {path}\n  build it first: python -m tendersure ingest \"<folder of workbooks>\"")
    store = BankStore(path)
    sheets = store.load_sheets()
    if not sheets:
        raise FlowError(f"the bank at {path} is empty; run ingest first")
    return sheets, store.template


def _parse_cached(path: str) -> List[Sheet]:
    import hashlib
    import pickle
    st = os.stat(path)
    key = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    cached = os.path.join(current().cache, key + ".pkl")
    if os.path.exists(cached):
        try:
            with open(cached, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass
    sheets = parse_workbook(path)
    os.makedirs(current().cache, exist_ok=True)
    with open(cached, "wb") as fh:
        pickle.dump(sheets, fh)
    return sheets


def past_categories(bank: str) -> List[str]:
    """Category descriptions from past exercises, for suggested additions."""
    if not os.path.exists(bank):
        return []
    from ..store import BankStore
    seen = {}
    for s in BankStore(bank).load_sheets():
        if s.description:
            seen.setdefault(" ".join(s.description.lower().split()), s.description.strip())
    return sorted(seen.values())


def groups_for(sheets: List[Sheet], cats: List[Category], client: str) -> Dict[str, str]:
    """Advert group per past sheet, via the category number at the end of its code."""
    by_num = {c.number: c.group for c in cats}
    out = {}
    for s in sheets:
        if s.client != client or not s.code:
            continue
        digits = "".join(ch for ch in s.code.split("-")[-1].split("_")[-1] if ch.isdigit())
        if digits and int(digits) in by_num:
            out[s.name] = by_num[int(digits)]
    return out


def read_marks(path: str) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = defaultdict(dict)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["code"].strip()][row["section"].strip()] = float(row["marks"])
    return out


# --- the learned files -----------------------------------------------------------

def audience(path: str, sheets: List[Sheet]) -> "gating.Book":
    """Who answers each question; seeded from the bank the first time."""
    entries = gating.load(path)
    if not entries:
        entries = gating.seed(trust.usable(sheets, trust.grade(sheets)))
        gating.save(entries, path)
        log.info(f"audience: seeded {len(entries)} questions from the bank -> {path}")
    return gating.Book(entries)


def glossary(path: str, sheets: List[Sheet]) -> "gl.Glossary":
    """The team's glossary; seeded from the bank the first time."""
    if os.path.exists(path):
        return gl.Glossary(gl.load(path))
    entries = gl.seed(trust.usable(sheets, trust.grade(sheets)))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    gl.save(entries, path)
    log.info(f"glossary: seeded {len(entries)} entries from the bank -> {path}")
    return gl.Glossary(entries)


def research_tools(research: str, sheets: List[Sheet], model: Model):
    """The research table, the bank's knowledge and (with a model) the web."""
    table = rs.load(research)
    bank = rs.BankKnowledge(trust.usable(sheets, trust.grade(sheets))) if sheets else None
    use_model, why_not = model.wanted()
    web = rs.WebResearcher(model=model.name) if use_model else None
    if web is None:
        log.info(f"research: our own data only ({why_not})")
    return table, bank, web
