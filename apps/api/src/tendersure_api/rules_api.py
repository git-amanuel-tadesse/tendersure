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
    action: str = Field(description="remove, replace, reword, limit or marks")
    match: str = Field(description="the phrase (remove, reword), the term (replace), the section (limit) or the sub-section (marks)")
    value: str = Field("", description="replace: the new term; reword: the new wording; limit: how many; marks: the sub-section's marks")
    groups: str = Field("", description="'' for all groups; words of group names, '!words' to skip them; comma-separated")
    note: str = Field("", description="why, in the team's words")
    problem: str = Field("", description="read only: why the rule cannot be applied, if it cannot")


class ClientRules(BaseModel):
    client: str = ""
    rules: List[ClientRule]
    suggested: List[ClientRule] = Field([], description="read only: rules proposed from the client's sample workbook "
                                                         "(spec 16 C), none an existing rule covers")


def _client(eid: str) -> str:
    try:
        return current_app.config["WORKSPACES"].get(eid).client
    except NotFound as e:
        abort(404, description=str(e))


def _path() -> str:
    return flows.current().learned("client_rules")


def _rule(r) -> ClientRule:
    return ClientRule(action=r.action, match=r.match, value=r.value, groups=r.groups, note=r.note, problem=r.problem() or "")


def _suggested(eid: str, client: str) -> list:
    """From the benchmark workbook, when one is chosen (spec 16 C)."""
    from .benchmark_api import chosen
    folder = current_app.config["WORKSPACES"].folder(eid)
    f = chosen(folder)
    if not f or not os.path.exists(os.path.join(folder, f)):
        return []
    return flows.suggest_from_samples(os.path.join(folder, f), client, _path())


def _out(client: str, rules, eid: str = "") -> dict:
    mine = [r for r in rules if r.client.upper() == client.upper()]
    return ClientRules(client=client, rules=[_rule(r) for r in mine],
                       suggested=[_rule(r) for r in _suggested(eid, client)] if eid else []).model_dump()


@bp.get("", responses={200: ClientRules})
def get_rules(path: ExercisePath):
    client = _client(path.exercise_id)
    return _out(client, flows.load_client_rules(_path()) if os.path.exists(_path()) else [], path.exercise_id)


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
    return _out(client, mine, path.exercise_id)


# --- the client's profile (spec 16 B): the house rules as a short form, stored as rules --------

pbp = APIBlueprint("profile", __name__, url_prefix="/api/exercises/<exercise_id>/profile", abp_tags=[Tag(name="rules")])


class ClientProfile(BaseModel):
    licence_term: str = Field("", description="business permit is written as this, e.g. 'trade license'")
    purchase_order: str = Field("", description="LPO is written as this, e.g. 'PO'")
    purchase_order_except: str = Field("", description="groups where LPO stays, e.g. 'engineering'")
    local_suppliers: bool = Field(False, description="local suppliers only: no sort code, no IBAN")
    no_conflict_of_interest: bool = Field(False, description="no conflict-of-interest question")
    office_evidence: str = Field("", description="the office evidence question's wording")
    reference_marks: str = Field("", description="Ref 1, Ref 2, Ref 3 marks, e.g. '15, 15, 10'")
    technical_limit: str = Field("", description="at most this many technical questions")


class ProfileOut(BaseModel):
    client: str
    country: str
    saved: bool = Field(description="false: no rule of the profile's shapes yet - the form shows the country's defaults")
    profile: ClientProfile
    defaults: ClientProfile


def _profile_out(st) -> dict:
    return ProfileOut(client=st.client, country=st.country, saved=st.saved,
                      profile=ClientProfile(**st.profile.as_dict()), defaults=ClientProfile(**st.defaults.as_dict())).model_dump()


@pbp.get("", responses={200: ProfileOut})
def get_profile(path: ExercisePath):
    ex = current_app.config["WORKSPACES"].get(path.exercise_id)
    return _profile_out(flows.profile_state(ex.client, ex.prefix, _path()))


@pbp.put("", responses={200: ProfileOut})
def set_profile(path: ExercisePath, body: ClientProfile):
    """Saved as this client's rules: those of the profile's shapes are replaced, every other rule kept."""
    ex = current_app.config["WORKSPACES"].get(path.exercise_id)
    return _profile_out(flows.save_profile(flows.Profile(**body.model_dump()), ex.client, ex.prefix, _path()))
