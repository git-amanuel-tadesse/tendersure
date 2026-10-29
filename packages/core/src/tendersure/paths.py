"""Where the project's runtime files live (.env, data/, .cache/, reports/).

The home is $TENDERSURE_HOME when set; otherwise the monorepo root, found by
walking up from this file to the pyproject.toml that carries a
[tool.tendersure] table. Installed outside the repo, it falls back to the
working directory.

Every location core uses comes from `current()`, read when it is needed,
never at import time. An app (the CLI, the API) that wants another home
calls `use(Paths(...))` once at start; tests point it at a temporary folder.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

MARKER = "[tool.tendersure]"


@lru_cache(maxsize=None)
def repo_root() -> Optional[str]:
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        p = os.path.join(d, "pyproject.toml")
        if os.path.exists(p):
            with open(p) as fh:
                if MARKER in fh.read():
                    return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def home() -> str:
    return os.environ.get("TENDERSURE_HOME") or repo_root() or os.getcwd()


@dataclass(frozen=True)
class Paths:
    home: str

    @property
    def env(self) -> str:
        return os.path.join(self.home, ".env")

    @property
    def data(self) -> str:
        """The bank and the learned files: client data, git-ignored."""
        return os.path.join(self.home, "data")

    @property
    def cache(self) -> str:
        return os.path.join(self.home, ".cache")

    @property
    def reports(self) -> str:
        return os.path.join(self.home, "reports")

    @property
    def outbox(self) -> str:
        """The audit log of every model request; $TENDERSURE_OUTBOX overrides it."""
        return os.environ.get("TENDERSURE_OUTBOX") or os.path.join(self.cache, "llm_outbox")

    @property
    def bank(self) -> str:
        return os.path.join(self.data, "bank.sqlite")

    @property
    def privacy_terms(self) -> str:
        return os.path.join(self.data, "privacy_terms.txt")

    def learned(self, name: str) -> str:
        """A learned file under data/: glossary, yesno_polarity, audience, rulings, research."""
        return os.path.join(self.data, f"{name}.csv")


_current: Optional[Paths] = None


def current() -> Paths:
    return _current or Paths(home())


def use(paths: Optional[Paths]):
    """Set the paths for this process; None goes back to the default home."""
    global _current
    _current = paths
