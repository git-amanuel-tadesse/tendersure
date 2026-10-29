"""The API's tests run on a temporary home, never the real data/, .cache/ or .env."""

import os
import tempfile

import pytest

from tendersure import flows

os.environ["TENDERSURE_NO_CACHE"] = "1"
flows.use(flows.Paths(tempfile.mkdtemp(prefix="tendersure-home-")))


@pytest.fixture
def web_dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>TenderSure</title>")
    (d / "assets" / "app.js").write_text("console.log('app')")
    return d


@pytest.fixture
def workspaces(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture
def client(web_dist, workspaces):
    from tendersure_api import create_app
    app = create_app(web_dist=str(web_dist), workspaces=str(workspaces))
    app.testing = True
    return app.test_client()


@pytest.fixture
def corpus(tmp_path_factory):
    """The synthetic history, in a folder of its own (a build reads every workbook under it)."""
    from tendersure.testing import write_corpus
    return write_corpus(tmp_path_factory.mktemp("corpus"))


@pytest.fixture
def home(tmp_path_factory):
    """A project home of this test's own (bank, reports, learned files), restored afterwards."""
    before = flows.current()
    h = tmp_path_factory.mktemp("home")
    flows.use(flows.Paths(str(h)))
    yield h
    flows.use(before)
