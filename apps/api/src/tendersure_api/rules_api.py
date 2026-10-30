"""/api/exercises/<id>/rules: the client's rules (spec 12 C), read and replaced as a whole.

They belong to the exercise's client, not the exercise: every exercise of that
client - and every later run - applies them. Stored in the learned table
client_rules (data/client_rules.csv), with every other client's rules kept.
"""

import os
from typing import List

from flask import abort, current_app
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from tendersure import flows

from .exercises import ExercisePath
from .workspaces import NotFound

bp = APIBlueprint("rules", __name__, url_prefix="/api/exercises/<exercise_id>/rules", abp_tags=[Tag(name="rules")])


class ClientRule(BaseModel):
    action: str = Field(description="remove, replace, reword or limit")
    match: str = Field(description="the phrase (remove, reword), the term (replace) or the section (limit)")
    value: str = Field("", description="replace: the new term; reword: the new wording; limit: how many")
    groups: str = Field("", description="'' for all groups; words of group names, '!words' to skip them; comma-separated")
    note: str = Field("", description="why, in the team's words")
    problem: str = Field("", description="read only: why the rule cannot be applied, if it cannot")


class ClientRules(BaseModel):
    client: str = ""
    rules: List[ClientRule]


def _client(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].get(eid).client
    except NotFound as e:
        abort(404, description=str(e))


def _path() -> str:
    return flows.current().learned("client_rules")


def _out(client: str, rules) -> dict:
    return ClientRules(client=client, rules=[ClientRule(action=r.action, match=r.match, value=r.value, groups=r.groups,
                                                        note=r.note, problem=r.problem() or "")
                                             for r in rules if r.client.upper() == client.upper()]).model_dump()


@bp.get("", responses={200: ClientRules})
def get_rules(path: ExercisePath):
    client = _client(path.exercise_id)
    return _out(client, flows.load_client_rules(_path()) if os.path.exists(_path()) else [])


@bp.put("", responses={200: ClientRules})
def set_rules(path: ExercisePath, body: ClientRules):
    """This client's rules, as a whole; other clients' rules are kept. A rule with a problem is
    kept and shown with it, and is not applied until fixed."""
    client = _client(path.exercise_id)
    others = [r for r in (flows.load_client_rules(_path()) if os.path.exists(_path()) else [])
              if r.client.upper() != client.upper()]
    mine = [flows.Rule(client, r.action.strip().lower(), r.match.strip(), r.value.strip(), r.groups.strip(), r.note.strip())
            for r in body.rules if r.match.strip()]
    flows.save_client_rules(others + mine, _path())
    return _out(client, mine)
