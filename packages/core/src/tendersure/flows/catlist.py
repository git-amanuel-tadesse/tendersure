"""The category list shaped by the team's comments (spec 21): the steps an app calls."""

import glob
import os
from typing import Dict, List, Optional, Sequence

from .. import categories as cat
from .. import catlist as cl
from .. import refine as rf
from ..paths import current
from ..privacy import register_clients
from .common import FlowError, Model, log

ListRow, ListChange, ListInstruction, ListReading, ListReference = cl.Row, cl.Change, cl.Instruction, cl.Reading, cl.Reference


def start_list(drafts: List[rf.Draft]) -> List[cl.Row]:
    """The list as the client wrote it (spec 21 A)."""
    return cl.from_drafts(drafts)


def approved_lists(tables: Sequence[str], exclude: str = "") -> List[cl.Reference]:
    """The team's approved lists for other clients, as sources a comment may name: the categories tables of
    other exercises, then data/*_categories.csv; one list per client, the first found kept."""
    seen: Dict[str, cl.Reference] = {}
    for path in list(tables) + sorted(glob.glob(os.path.join(current().data, "*_categories.csv"))):
        if not os.path.exists(path):
            continue
        rows = cat.load(path)
        client = next((c.client for c in rows if c.client), "") or os.path.basename(path).split("_")[0].upper()
        if not rows or client.upper() == exclude.upper() or client.upper() in seen:
            continue
        seen[client.upper()] = cl.Reference(client, [(c.group or "", c.description, list(c.sub_categories))
                                                     for c in rows])
    return list(seen.values())


def read_comment(comment: str, rows: List[cl.Row], drafts: List[rf.Draft], refs: List[cl.Reference], client: str,
                 brief: str = "", model: Model = Model()) -> cl.Reading:
    """A team member's comment read by the model as checked changes to the list (spec 21 B, C)."""
    if not comment.strip():
        raise FlowError("write a comment first")
    use, why_not = model.wanted()
    if not use:
        raise FlowError(f"comments need the AI, and it is off: {why_not}")
    register_clients([client] + [r.client for r in refs])
    log.info(f"list: reading “{comment[:80]}” on {len(rows)} categories, with "
             f"{', '.join(r.client for r in refs) or 'no'} approved lists")
    got = cl.read(comment, rows, drafts, refs, client, brief, model.name)
    status = ", ".join(f"{i.status}" for i in got.instructions) or "no instructions"
    log.info(f"list: {len(got.instructions)} instructions ({status}), {len(got.changes)} changes, "
             f"{sum(bool(c.risky) for c in got.changes)} flagged")
    return got


def apply_changes(rows: List[cl.Row], changes: Sequence[cl.Change]) -> List[cl.Row]:
    return cl.apply(rows, changes)


def check_list(rows: List[cl.Row], target_file: str, sheets: Optional[Sequence[str]] = None) -> Dict:
    """How close the list is to a list the team made (spec 21 F)."""
    try:
        target = rf.read_draft(target_file, list(sheets or []) or None)
    except (KeyError, ValueError) as e:
        raise FlowError(f"{os.path.basename(target_file)}: {e}")
    return cl.compare(rows, [((d.group or "").strip(), d.description, d.sub_categories) for d in target])


def list_categories(rows: List[cl.Row], drafts: List[rf.Draft], client: str,
                    version: Optional[str] = None) -> List[cat.Category]:
    """The categories table criteria generation reads (spec 21 G)."""
    return cl.to_categories(rows, drafts, client, version)


def export_client_list(path: str, rows: List[cl.Row]):
    cl.write_client_workbook(path, rows)
