"""Where the project's runtime files live (.env, data/).

The home is $TENDERSURE_HOME when set; otherwise the monorepo root, found by
walking up from this file to the pyproject.toml that carries a
[tool.tendersure] table. Installed outside the repo, it falls back to the
working directory.
"""

import os
from functools import lru_cache

MARKER = "[tool.tendersure]"


@lru_cache(maxsize=None)
def _repo_root():
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
    return os.environ.get("TENDERSURE_HOME") or _repo_root() or os.getcwd()
