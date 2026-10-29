"""Core's tests run on synthetic workbooks (tendersure.testing), never on client data."""

import os
import tempfile

os.environ["TENDERSURE_NO_CACHE"] = "1"      # throwaway test workbooks must not fill .cache/
import pytest

from tendersure import paths

# Tests never read or write the real data/, .cache/, outbox or .env: their home is a temp folder.
paths.use(paths.Paths(tempfile.mkdtemp(prefix="tendersure-home-")))

from tendersure.testing import write_corpus


@pytest.fixture
def corpus(tmp_path):
    """Two clients, goods and service categories, as on-disk workbooks."""
    write_corpus(tmp_path)
    return tmp_path


@pytest.fixture
def real_corpus():
    """Client workbooks: $TENDERSURE_CORPUS, else the project's 'sample file' folder."""
    here = os.environ.get("TENDERSURE_CORPUS") or os.path.join(paths.repo_root() or os.getcwd(), "sample file")
    if not os.path.isdir(here) or not any(f.endswith(".xlsx") for f in os.listdir(here)):
        pytest.skip("client sample workbooks not present")
    return here
