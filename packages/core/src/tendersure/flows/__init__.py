"""The steps every app runs: the CLI and the API call these, never the rule
modules directly (spec 06 M1).

Each step reports through the `tendersure` logger and returns a result; it
never prints and never exits (FlowError when it cannot go on).
"""

from .common import FlowError, Model, load_corpus, load_source, log
from .generate import Result as GenerateResult, Settings as GenerateSettings, generate
from .ingest import IngestResult, ingest
from .refine import ApplyResult, ProposeResult, apply, apply_file, propose, propose_file

__all__ = ["FlowError", "Model", "load_corpus", "load_source", "log",
           "ingest", "IngestResult",
           "propose", "propose_file", "apply", "apply_file", "ProposeResult", "ApplyResult",
           "generate", "GenerateSettings", "GenerateResult"]
