"""The client's own requirements for a category (spec 07 R4): the licence,
key staff and technical items its draft lists, asked by rules in every
sheet of the category, with or without a model.

- Licence: a contractor grade ("min G5/BC5 or above") is asked as the grade
  typed in plus the grade certificate, in the Eligibility section; any other
  licence ("Consultancy certification") as an Eligibility document.
- HR: one evidence row per role. The skeleton's own role rows are reworded
  in order, keeping their marks (the client's benchmark); roles beyond them
  arrive with 0 marks; skeleton roles left over go, their marks kept in the
  section.
- Technical: one upload per item, replacing a matching skeleton row (and its
  marks) or added with 0 marks; an equipment list asks for ownership or rent
  evidence.

Marks are not rebalanced across sections: a row added with 0 marks leaves
the sheet at 100, and the QA report lets the team move marks.
"""

import re
from typing import List, Optional, Tuple

from .parser import Row, section_key
from .validate import asks_for_document

GRADE_ITEM = re.compile(r"\b(grade|level|class)\b|\bor\s+above\b|\bmin(imum)?\b", re.I)
GRADE_ROW = re.compile(r"\bgrade\b|competenc|contractor\s+(level|class|category)", re.I)
ROLE_ROW = re.compile(r"\b(cvs?|curriculum\s+vitae|resume)\b|evidence\b.*\bfor\b", re.I)
OWNERSHIP_ROW = re.compile(r"ownership|rent(al)?\s+agreement|lease", re.I)
_YEARS = re.compile(r"\s*(?:min(?:imum)?\.?|at\s+least)?\s*(\d+)\+?\s*(?:years?|yrs?)\b.*$", re.I)
_PLAIN = {"evidence", "letter", "for", "min", "minimum", "year", "years", "please", "attach", "of", "and", "the",
          "a", "an", "with", "copy", "valid", "your", "from", "set", "up", "or", "related", "any"}
HR_LABEL = "Human Resource- HRM"
TECH_LABEL = "Technical Specifications- TECH"
GRADE_QUESTION = "Please specify your contractor grade/level"
SOURCE = "categories table"


def _new_row(criteria: str, short_name: Optional[str] = None, answer_type: int = 5, mandatory: bool = True) -> Row:
    return Row(source_file="", sheet="", excel_row=0, criteria=criteria, marks=0.0,
               archive_short_name=short_name, answer_type=answer_type, mandatory=mandatory,
               compliance_attach=answer_type == 5)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in _PLAIN}


# --- licence: into the Eligibility section ------------------------------------

def licence_rows(plan, in_section) -> list:
    """The rows the category's licence items need in the Eligibility section:
    a grade row the sheet has elsewhere with no marks is moved there; a
    missing one is added. Each carries its note."""
    from .generate import ADAPTED, PlannedRow
    grades = [x for x in plan.target.licence if GRADE_ITEM.search(x)]
    documents = [x for x in plan.target.licence if x not in grades]
    asked = " ".join(p.row.criteria.lower() for p in plan.questions)
    out = []

    def add(row: Row, why: str):
        out.append(PlannedRow(kind="question", row=row, origin=ADAPTED, source=SOURCE,
                              notes=[f"from the category's licence column: {why}"]))

    if grades:
        found = [p for p in plan.questions if GRADE_ROW.search(p.row.criteria) and p not in in_section
                 and not p.row.marks]
        typed = next((p for p in found if not asks_for_document(p.row)), None)
        there = [p for p in in_section if GRADE_ROW.search(p.row.criteria) and asks_for_document(p.row)]
        uploads = there + [p for p in found if asks_for_document(p.row)]
        if typed:
            typed.note("from the category's licence column: the grade, asked in Eligibility")
            out.append(typed)
        elif not any(GRADE_ROW.search(p.row.criteria) and not asks_for_document(p.row) for p in in_section):
            add(_new_row(GRADE_QUESTION, "Contractor Grade", answer_type=1), "the grade")
        for item, p in zip(grades, uploads + [None] * len(grades)):
            need = f"(required: {item})"
            if p is not None:
                if item.lower() not in p.row.criteria.lower():
                    p.note(f"requirement added to the wording: {need}")
                    p.row.criteria = f"{p.row.criteria.rstrip(' .')} {need}"
                p.note("from the category's licence column")
                if p not in in_section:          # already in Eligibility: stays where it is
                    out.append(p)
            else:
                add(_new_row(f"Please attach a valid grade/competence certificate from the relevant authority {need}",
                             "Grade Certificate"), item)
    for item in documents:
        there = next((p for p in plan.questions if item.lower() in p.row.criteria.lower()
                      and asks_for_document(p.row) and not p.row.marks), None)
        if there is None and item.lower() not in asked:
            add(_new_row(f"Please attach a valid {_lower_first(item)}",
                         item if len(item.split()) <= 4 else None), item)
        elif there is not None and there not in in_section:      # asked elsewhere: an Eligibility document
            there.note(f"from the category's licence column: {item}, asked in Eligibility")
            out.append(there)
    return out


# --- HR and technical ----------------------------------------------------------

def _role_text(role: str) -> str:
    m = _YEARS.search(role)
    name = role[:m.start()].strip(" ,") if m else role
    years = f" with at least {m.group(1)} years of experience" if m else ""
    return f"Attach evidence (CV, contract and education/experience) for {name}{years}"


def _section_rows(plan, key: str) -> List[Tuple[int, object]]:
    return [(i, p) for i, p in enumerate(plan.rows) if p.kind == "question" and section_key(p.row.section) == key]


def _set_upload(p, criteria: str, why: str):
    r = p.row
    if r.criteria != criteria:
        p.note(f"{why}; was: {r.criteria}")
        r.criteria = criteria
    else:
        p.note(why, adapted=False)
    if r.answer_type != 5:
        r.answer_type, r.options, r.option_scores = 5, None, None
    if not (r.attachment or r.compliance_attach):
        r.compliance_attach = True


def place_hr(plan):
    from .generate import ADAPTED, drop_row, insert_rows, keep_section_totals
    roles = plan.target.hr
    if not roles:
        return
    before = {"hr": plan.section_marks().get("hr", (0, 0, 0))[0]}
    existing = [p for _, p in _section_rows(plan, "hr") if asks_for_document(p.row) and ROLE_ROW.search(p.row.criteria)]
    # a role row that already names a listed role stays with it; the rest pair up in order
    paired, free = {}, list(existing)
    for role in roles:
        name = _words(role)
        hit = next((p for p in free if name and name <= _words(p.row.criteria)), None)
        if hit:
            paired[role] = hit
            free.remove(hit)
    for role in roles:
        if role not in paired and free:
            paired[role] = free.pop(0)
    for role, p in paired.items():
        _set_upload(p, _role_text(role), f"from the category's HR column ({role})")
    extra = [role for role in roles if role not in paired]
    if extra:
        template = existing[0].row if existing else None
        new = []
        for role in extra:
            r = _new_row(_role_text(role), mandatory=template.mandatory if template else True)
            if template is not None:
                r.attachment, r.compliance_attach = template.attachment, template.compliance_attach
            r.section = HR_LABEL
            new.append(r)
        insert_rows(plan, "hr", new, ADAPTED, SOURCE,
                    "from the category's HR column; 0 marks: give it marks if the role should score",
                    section_label=HR_LABEL)
    for p in free:
        drop_row(plan, plan.rows.index(p), f"role not in the category's HR column ({', '.join(roles)})")
    if free:
        for problem in keep_section_totals(plan, before, "the category's HR column"):
            plan.notes.append(f"HR marks: {problem}")


def _technical_items(items: List[str]) -> List[str]:
    """An equipment list in brackets is one question per piece (spec 12 R2): "(Dump Truck,
    Pick up)" -> "(Dump Truck)", "(Pick up)"."""
    out = []
    for item in items:
        if item.startswith("("):
            out += [f"({x.strip()})" for x in item.strip("()").split(",") if x.strip()]
        else:
            out.append(item)
    return out


def _technical_text(item: str) -> str:
    if item.startswith("("):
        return f"Please attach evidence of ownership or rent agreement for a {item.strip('()').strip()}"
    return f"Please attach {_lower_first(item)}"


def place_technical(plan):
    """The category's technical items are its whole Technical section (spec 12 R2): a past
    question matching an item is reworded to it, every other technical question goes, and
    the section's marks are shared equally by the items."""
    from .generate import ADAPTED, apply_section_marks, drop_row, insert_rows
    items = _technical_items(plan.target.technical)
    if not items:
        return
    before = plan.section_marks().get("technical", (0, 0, 0))[0]
    free = [p for _, p in _section_rows(plan, "technical")]
    kept, new = [], []
    for item in items:
        if item.startswith("("):
            hit = next((p for p in free if OWNERSHIP_ROW.search(p.row.criteria)), None)
        else:
            want = _words(item)
            hit = next((p for p in free if want and len(want & _words(f"{p.row.criteria} {p.row.archive_short_name or ''}"))
                        * 2 >= len(want)), None)
        if hit:
            free.remove(hit)
            kept.append(hit)
            _set_upload(hit, _technical_text(item), f"from the category's technical column ({item})")
        else:
            r = _new_row(_technical_text(item), mandatory=False)
            r.section = TECH_LABEL
            new.append(r)
    for p in free:                                   # not one of the client's items
        drop_row(plan, plan.rows.index(p), "the category's technical column lists the whole Technical section")
    if new:
        insert_rows(plan, "technical", new, ADAPTED, SOURCE, "from the category's technical column",
                    section_label=TECH_LABEL)
    if before:
        for _, p in _section_rows(plan, "technical"):
            p.row.marks, p.weight_hint = 0.0, 1.0           # equal shares of the section's marks
            p.row.system = p.row.compliance = None
        apply_section_marks(plan, {"technical": before}, "the category's technical column")


def stated(category) -> str:
    """The items as the model reads them (the proposer and the reviewer), so
    the rest of the sheet is worded to fit; the rows themselves are added by
    the rules above."""
    parts = [(label, getattr(category, name)) for name, label in
             (("licence", "licence / contractor grade"), ("hr", "key staff"), ("technical", "technical documents and equipment"))]
    text = "; ".join(f"{label}: {', '.join(items)}" for label, items in parts if items)
    return f"{text} (rows asking for each are added automatically; do not add them)" if text else ""


def place_requirements(plan):
    """HR and technical rows from the categories table (licence rows join
    the Eligibility section with place_eligibility)."""
    place_hr(plan)
    place_technical(plan)
