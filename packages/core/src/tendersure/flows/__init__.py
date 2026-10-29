"""The steps every app runs: the CLI and the API call these, never the rule
modules directly (spec 06 M1).

Each step reports through the `tendersure` logger and returns a result; it
never prints and never exits (FlowError when it cannot go on; LLMError and
PrivacyError when a model step stops). Draft, Line, Category and Item are the
steps' inputs and outputs, so they are part of this API too.
"""

from ..categories import Category
from ..llm import LLMError
from ..paths import Paths, current, use
from ..privacy import PrivacyError
from ..refine import Draft, Line
from ..review import Item
from .categories import (CategoriesResult, ENGINES, RowProblem, check_table, extract as extract_categories,
                         gate_problem, normalise as normalise_category)
from .check import CheckResult, check
from .common import FlowError, Model, ModelStatus, load_corpus, load_source, log, model_status
from .evaluate import ModelEvaluation, PrivacyCheck, evaluate, evaluate_model, privacy_check
from .generate import Result as GenerateResult, Settings as GenerateSettings, generate, read_review
from .ingest import BankStatus, IngestResult, bank_status, ingest
from .learn import GlossaryResult, LearnResult, apply_review as apply_learned_review, learn, seed_glossary
from .refine import (ApplyResult, ProposeResult, apply, apply_file, draft_tabs, export_final, export_revised,
                     import_revised, load_categories, propose, propose_file, read_draft, save_categories)
from .refine import evaluate as evaluate_refinement
from .research import ResearchResult, apply_review as apply_research_review, research

__all__ = [
    "FlowError", "LLMError", "PrivacyError", "Model", "Paths", "current", "use", "log",
    "Draft", "Line", "Category", "Item",
    "load_corpus", "load_source",
    "ingest", "IngestResult", "bank_status", "BankStatus", "model_status", "ModelStatus",
    "extract_categories", "CategoriesResult", "ENGINES",
    "check_table", "gate_problem", "normalise_category", "RowProblem",
    "propose", "propose_file", "apply", "apply_file", "evaluate_refinement", "ProposeResult", "ApplyResult",
    "draft_tabs", "read_draft", "export_revised", "import_revised", "export_final",
    "save_categories", "load_categories",
    "research", "apply_research_review", "ResearchResult",
    "learn", "apply_learned_review", "seed_glossary", "LearnResult", "GlossaryResult",
    "generate", "read_review", "GenerateSettings", "GenerateResult",
    "check", "CheckResult",
    "evaluate", "evaluate_model", "privacy_check", "ModelEvaluation", "PrivacyCheck",
]
