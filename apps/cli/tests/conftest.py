"""The CLI's tests run on the synthetic workbooks, never the real data/, .cache/ or .env."""

import os
import tempfile

import pytest

from tendersure import flows
from tendersure.testing import write_corpus

os.environ["TENDERSURE_NO_CACHE"] = "1"
flows.use(flows.Paths(tempfile.mkdtemp(prefix="tendersure-home-")))


@pytest.fixture
def corpus(tmp_path_factory):
    """The synthetic history, in a folder of its own (a build reads every workbook under it)."""
    return write_corpus(tmp_path_factory.mktemp("corpus"))
