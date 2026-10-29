"""The API's shapes (spec 06 M2): pydantic models mapped from the flows'
dataclasses. Core keeps plain dataclasses and never learns about JSON; the
OpenAPI document - and the web app's generated types - come from these.

Each model mirroring a core type lists exactly that type's fields (a test
fails when they drift apart) and maps both ways: `of(obj)` and `core()`.
Derived values (a line's gate question, a category's kind) are read-only.
"""

import copy
import dataclasses
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, computed_field

from tendersure import flows


class CoreModel(BaseModel):
    """A pydantic mirror of one core dataclass."""
    CORE: ClassVar[type]

    @classmethod
    def of(cls, obj):
        return cls.model_validate(dataclasses.asdict(obj))

    def core(self):
        # read the fields, not model_dump(): that would evaluate the derived
        # values, which are themselves computed from core()
        return self.CORE(**{f.name: copy.deepcopy(getattr(self, f.name)) for f in dataclasses.fields(self.CORE)})


class Draft(CoreModel):
    """A row of a client's draft category list."""
    CORE: ClassVar[type] = flows.Draft
    id: str = Field(description="the draft's own number, or r<row> when it has none")
    description: str
    group: Optional[str] = None
    sub_categories: List[str] = []
    licence: List[str] = Field([], description="the draft's licence / contractor level items")
    hr: List[str] = Field([], description="the draft's key staff items")
    technical: List[str] = Field([], description="the draft's technical documents and equipment")
    individual: bool = Field(False, description="the row says Individual Consultants may apply")
    notes: List[str] = Field([], description="questions for the client found while reading the row")


class Line(CoreModel):
    """A line of the revised category list: one proposed category, with how it came from the draft."""
    CORE: ClassVar[type] = flows.Line
    description: str
    status: str = Field(description="Kept, Renamed, Merged, Separated, Repeated, Additional or Removed")
    from_draft: List[str]
    group: Optional[str] = None
    reason: str = ""
    question: str = Field("", description="a question for the client about this line")
    sub_categories: List[str] = []
    eligibility: List[str] = Field([], description="stated by the draft: applied")
    applies_to: List[str] = []
    code: str = ""
    decision: str = Field("", description="the review: accept, reject, remove or edit (blank accepts)")
    comment: str = Field("", description="the client's comment; with edit, the new wording")
    suggested_eligibility: List[str] = Field([], description="the model's or research's: shown, not applied")
    technical_name: str = ""
    usual_split: List[str] = []
    research_sources: List[str] = []
    licence: List[str] = Field([], description="the draft's licence / contractor level items")
    hr: List[str] = Field([], description="the draft's key staff items")
    technical: List[str] = Field([], description="the draft's technical documents and equipment")

    @computed_field
    @property
    def gate_question(self) -> bool:
        return self.core().gate_question


class Category(CoreModel):
    """A row of the categories table generation reads."""
    CORE: ClassVar[type] = flows.Category
    number: int
    description: str
    group: Optional[str] = None
    sub_categories: List[str] = []
    client: Optional[str] = None
    version: Optional[str] = None
    requirements: str = ""
    eligibility: List[str] = Field([], description="documents asked in the Eligibility section")
    gate_question: bool = Field(False, description="the Organization type question gates the sheet")
    applies_to: List[str] = Field([], description="organization types this category accepts")
    licence: List[str] = Field([], description="licence / contractor grade, asked in the Eligibility section")
    hr: List[str] = Field([], description="key staff, one evidence row each in Human Resource")
    technical: List[str] = Field([], description="documents and equipment, asked in Technical Specifications")

    @computed_field
    @property
    def kind(self) -> str:
        return self.core().kind


class Item(CoreModel):
    """A line of the Decisions tab: one decision for the team, with its answer once given."""
    CORE: ClassVar[type] = flows.Item
    id: str = Field(description="Q-, A-, M-, C-, N-, T- or F- followed by what it is about")
    type: str
    decide: str
    codes: List[str] = Field(description="the sheets it concerns")
    proposed: str
    why: str = ""
    answer: str = Field("", description="the answer, in the Decisions tab's grammar (blank accepts the proposal)")
    keys: List[str] = []
    status: str = ""
    ruling: Optional[Dict[str, Any]] = None
    payload: Optional[List[Any]] = None


# --- step results ---------------------------------------------------------------

class IngestResult(BaseModel):
    added: int
    sheets_added: int
    unchanged: int
    replaced: int
    sheets: int
    questions: int
    findings: Dict[str, int]
    tiers: Dict[str, int]
    bank_questions: int
    conflicts: int
    reports: Dict[str, str]

    @classmethod
    def of(cls, r: "flows.IngestResult"):
        return cls.model_validate(dataclasses.asdict(r))


class ProposeResult(BaseModel):
    drafts: List[Draft]
    lines: List[Line]
    target: Optional[List[int]] = Field(None, description="[low, high] category count aimed at")
    out: Optional[str] = None
    proposed: int
    statuses: Dict[str, int]
    questions: int

    @classmethod
    def of(cls, r: "flows.ProposeResult"):
        return cls(drafts=[Draft.of(d) for d in r.drafts], lines=[Line.of(ln) for ln in r.lines],
                   target=list(r.target) if r.target else None, out=r.out, proposed=r.proposed,
                   statuses=r.statuses, questions=r.questions)


class ClientNote(BaseModel):
    line: Line
    comment: str
    change: str = Field(description="the model's proposed change: shown, never applied")


class ApplyResult(BaseModel):
    final: List[Line]
    excluded: List[Line]
    notes: List[ClientNote]
    problems: List[str]
    categories: List[Category]
    out: Optional[str] = None
    categories_out: Optional[str] = None

    @classmethod
    def of(cls, r: "flows.ApplyResult"):
        return cls(final=[Line.of(ln) for ln in r.final], excluded=[Line.of(ln) for ln in r.excluded],
                   notes=[ClientNote(line=Line.of(ln), comment=c, change=ch) for ln, c, ch in r.notes],
                   problems=r.problems, categories=[Category.of(c) for c in r.categories],
                   out=r.out, categories_out=r.categories_out)


class NotWritten(BaseModel):
    code: str
    reason: str


class GenerateResult(BaseModel):
    written: List[str] = Field(description="the sheet codes written")
    failed: List[NotWritten]
    files: List[str]
    qa: str
    learned_review: Optional[str] = None
    items: List[Item]
    open: Dict[str, int] = Field(description="open decisions by kind: Q, A, M, C, N, T, F")
    review_log: List[str] = []
    review_problems: List[str] = []

    @classmethod
    def of(cls, r: "flows.GenerateResult"):
        from collections import Counter
        return cls(written=r.written, failed=[NotWritten(code=c, reason=m) for c, m in r.failed], files=r.files,
                   qa=r.qa, learned_review=r.learned_review, items=[Item.of(i) for i in r.items],
                   open=dict(Counter(i.id[0] for i in r.open_items)), review_log=r.review_log,
                   review_problems=r.review_problems)
