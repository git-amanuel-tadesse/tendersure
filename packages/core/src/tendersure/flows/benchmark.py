"""The client's benchmark (spec 08) for the apps: its tabs as checked, and
the group -> tab map proposed from the categories table, kept as corrected."""

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .. import benchmark as bm
from .. import categories as cat
from .common import FlowError

Tab, MapLine = bm.Tab, bm.MapLine


@dataclass
class BenchmarkState:
    tabs: List[bm.Tab]
    lines: List[bm.MapLine] = field(default_factory=list)


def benchmark_state(path: str, client: str, categories: Optional[str] = None,
                    map_path: Optional[str] = None) -> BenchmarkState:
    """The tabs, and the map: every line already saved, plus a proposal for
    each group of the categories table that has none (saved too)."""
    if not os.path.exists(path):
        raise FlowError(f"no benchmark workbook at {path}")
    try:
        tabs = bm.load(path, client)
    except Exception as e:                     # not a workbook the parser can read
        raise FlowError(f"{os.path.basename(path)} is not a readable criteria workbook ({e})")
    if not tabs:
        raise FlowError(f"{os.path.basename(path)} has no criteria tabs")
    known = bm.load_map(map_path) if map_path else []
    cats = cat.load(categories) if categories and os.path.exists(categories) else []
    lines = bm.propose_map(cats, tabs, client, known)
    if map_path and len(lines) > len(known):
        bm.save_map(lines, map_path)
    return BenchmarkState(tabs, lines)


def save_benchmark_map(lines: List[bm.MapLine], tabs: List[bm.Tab], path: str) -> List[bm.MapLine]:
    """The team's map; a line naming a tab the workbook lacks is refused."""
    names = {t.name for t in tabs}
    bad = [m.tab for m in lines if m.tab not in names]
    if bad:
        raise FlowError(f"no tab {', '.join(repr(b) for b in bad)} in the benchmark (it has: {', '.join(sorted(names))})")
    bm.save_map(lines, path)
    return lines
