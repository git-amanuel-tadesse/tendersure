"""The client's rules as the apps see them (spec 12 C, 16 B-C): the profile form,
and rules proposed from the client's own sample workbook."""

import os
from dataclasses import dataclass
from typing import List, Optional

from .. import benchmark as bm
from .. import client_rules as cr
from .. import profile as pf
from .. import suggest_rules as sr
from ..paths import current
from ..research import country_of

Profile = pf.Profile


@dataclass
class ProfileState:
    client: str
    country: str
    saved: bool                 # False: no rule of the profile's shapes yet; `profile` is the country's defaults
    profile: Profile
    defaults: Profile


def _rules_file(path: Optional[str]) -> str:
    return path or current().learned("client_rules")


def _load(path: str) -> List[cr.Rule]:
    return cr.load(path) if os.path.exists(path) else []


def profile_state(client: str, prefix: str, client_rules: Optional[str] = None) -> ProfileState:
    path = _rules_file(client_rules)
    mine = [r for r in _load(path) if r.client.upper() == client.upper()]
    p, saved = pf.read(mine)
    country = country_of(prefix.rstrip("-_") + "-")
    d = pf.defaults(country)
    return ProfileState(client, country, saved, p if saved else d, d)


def save_profile(profile: Profile, client: str, prefix: str, client_rules: Optional[str] = None) -> ProfileState:
    """Saved as this client's rules: those of the profile's shapes are replaced, every other rule kept."""
    path = _rules_file(client_rules)
    cr.save(pf.save_into(profile, _load(path), client), path)
    return profile_state(client, prefix, client_rules)


def suggest_from_samples(workbook: str, client: str, client_rules: Optional[str] = None) -> List[cr.Rule]:
    """Rules the client's sample workbook proposes, none an existing rule covers; nothing when it cannot be read."""
    try:
        tabs = [t.sheet for t in bm.load(workbook, client) if t.usable]
    except Exception:              # the benchmark card says why a workbook cannot be read
        return []
    existing = cr.for_client(_load(_rules_file(client_rules)), client)
    return sr.new_only(sr.from_samples(tabs, client), existing)
