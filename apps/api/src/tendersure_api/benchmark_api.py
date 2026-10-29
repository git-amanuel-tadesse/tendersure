"""/api/exercises/<id>/benchmark: the client's sample criteria (spec 08).

    benchmark.json        which uploaded workbook is the benchmark
    benchmark_map.csv     group | kind | tab: proposed for groups without a line, kept as the team sets it

Generation runs read both: a category whose group is mapped to a usable tab
starts from it.
"""

import json
import os
from typing import List, Optional

from flask import abort, current_app
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .refine_api import CATEGORIES, _exercise, _flow, _upload
from .workspaces import NotFound

bp = APIBlueprint("benchmark", __name__, url_prefix="/api/exercises/<exercise_id>/benchmark",
                  abp_tags=[Tag(name="benchmark")])
STATE, MAP = "benchmark.json", "benchmark_map.csv"


class BenchmarkTab(BaseModel):
    name: str
    total: float
    usable: bool
    refused: List[str] = Field([], description="why the tab is not used; fix it in the client's file")
    warnings: List[str] = Field([], description="left to generation's corrections and its pre-write check")


class MapLine(BaseModel):
    group: str
    kind: str = Field("", description="'' for the whole group; else goods, service, works or consultancy")
    tab: str
    source: str = Field("team", description="proposed (by the system) or team")


class Benchmark(BaseModel):
    file: Optional[str] = Field(None, description="the uploaded workbook; none when no benchmark is used")
    tabs: List[BenchmarkTab] = []
    map: List[MapLine] = []


class ChooseBenchmark(BaseModel):
    file: str = Field(description="an uploaded workbook, e.g. uploads/PSI Criteria Draft.xlsx")


class MapLines(BaseModel):
    lines: List[MapLine]


def _folder(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].folder(eid)
    except NotFound as e:
        abort(404, description=str(e))


def chosen(folder: str) -> Optional[str]:
    """The benchmark workbook's relative path, if one is chosen."""
    p = os.path.join(folder, STATE)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh).get("file")


def settings_for(eid: str, folder: str) -> dict:
    """What a generation run passes on: the workbook and its map, when one is chosen."""
    rel = chosen(folder)
    return {"benchmark": _upload(eid, rel), "benchmark_map": os.path.join(folder, MAP)} if rel else {}


def _state(eid: str) -> dict:
    folder = _folder(eid)
    rel = chosen(folder)
    if not rel:
        return Benchmark().model_dump()
    s = _flow(flows.benchmark_state, _upload(eid, rel), _exercise(eid).client, os.path.join(folder, CATEGORIES),
              os.path.join(folder, MAP))
    return Benchmark(file=rel, tabs=[BenchmarkTab(name=t.name, total=t.total, usable=t.usable, refused=t.refused,
                                                  warnings=t.warnings) for t in s.tabs],
                     map=[MapLine(**vars(m)) for m in s.lines]).model_dump()


@bp.get("", responses={200: Benchmark})
def get_benchmark(path: ExercisePath):
    return _state(path.exercise_id)


@bp.post("", responses={200: Benchmark})
def choose_benchmark(path: ExercisePath, body: ChooseBenchmark):
    """Use an uploaded workbook as the benchmark: its tabs are checked and the map proposed."""
    eid = path.exercise_id
    folder = _folder(eid)
    src = _upload(eid, body.file)
    _flow(flows.benchmark_state, src, _exercise(eid).client)              # readable, before it is kept
    if chosen(folder) != body.file and os.path.exists(os.path.join(folder, MAP)):
        os.remove(os.path.join(folder, MAP))                              # another workbook's tabs
    with open(os.path.join(folder, STATE), "w") as fh:
        json.dump({"file": body.file}, fh)
    return _state(eid)


@bp.put("/map", responses={200: Benchmark})
def set_map(path: ExercisePath, body: MapLines):
    """The whole map as the team set it; a group left out is proposed again."""
    eid = path.exercise_id
    folder = _folder(eid)
    rel = chosen(folder)
    if not rel:
        abort(409, description="choose the benchmark workbook first")
    s = _flow(flows.benchmark_state, _upload(eid, rel), _exercise(eid).client)
    _flow(flows.save_benchmark_map, [flows.MapLine(**m.model_dump()) for m in body.lines], s.tabs,
          os.path.join(folder, MAP))
    return _state(eid)


@bp.delete("", responses={200: Benchmark})
def stop_benchmark(path: ExercisePath):
    """Build from the bank again; the uploaded workbook stays in uploads/."""
    folder = _folder(path.exercise_id)
    for name in (STATE, MAP):
        if os.path.exists(os.path.join(folder, name)):
            os.remove(os.path.join(folder, name))
    return Benchmark().model_dump()
