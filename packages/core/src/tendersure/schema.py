"""Schema definitions for TenderSure prequalification criteria sheets.

The answer-type codes below were INFERRED from 66 existing criteria sheets
across three clients (ERCS, SNV, ILRI). They are not documented by the
platform. Confirm with the TenderSure team before relying on them.
"""

ANSWER_TYPES = {
    1: {
        "name": "Text",
        "supplier_sees": "A free-text box",
        "requires_options": False,
        "requires_attachment": False,
        "example": "Insert TIN or Tax identification number",
    },
    2: {
        "name": "Single select",
        "supplier_sees": "A list where one option can be chosen",
        "requires_options": True,
        "requires_attachment": False,
        "example": "Organization type (sole proprietor, plc, share company)",
    },
    3: {
        "name": "Multi select",
        "supplier_sees": "A list where several options can be chosen",
        "requires_options": True,
        "requires_attachment": False,
        "example": "Please select the subcategory you would like to participate in.",
    },
    4: {
        "name": "Yes/No",
        "supplier_sees": "A yes/no toggle",
        "requires_options": False,
        "requires_attachment": False,
        "example": "Do you offer extended warranty on goods sold Yes/No",
    },
    5: {
        "name": "Attachment",
        "supplier_sees": "A file upload control",
        "requires_options": False,
        "requires_attachment": True,
        "example": "Attach TIN certificate or equivalent tax registration certificate",
    },
}

# Column positions of the 14/16-column layout. The parser reads fields by
# header (see LAYOUT) and falls back to these only when a header is unknown.
# Header sits on the row where column B reads "Section".
COLUMNS = {
    "section": 2,          # B
    "sub_section": 3,      # C
    "criteria": 4,         # D
    "marks": 5,            # E
    "system": 6,           # F
    "compliance": 7,       # G
    "archive_short_name": 8,   # H
    "acronym": 9,          # I
    "answer_type": 10,     # J
    "mandatory": 11,       # K
    "attachment": 12,      # L  - upload, NOT compliance scored
    "compliance_attach": 13,   # M  - upload that carries compliance marks
    "options": 14,         # N
    "option_scores": 15,   # O
    "gate_question": 16,   # P  - SNV sheets only
    "applies_to": 17,      # Q  - SNV sheets only
}

# The template every sheet is written in (B..V), from the 2026 NRC exercise.
# Older sheets have 14 columns (ERCS, ILRI) or 16 (SNV: + Gate Question,
# Applies To); the parser finds fields by header, so all three read alike.
# Evaluation, Eval Options, Eval Options Scores and Verify Against belong to
# the Kenya template: written as headers, left empty, not read.
LAYOUT = [
    ("section", "Section"),
    ("sub_section", "Sub Section"),
    ("criteria", "Criteria"),
    ("marks", "Marks"),
    ("eligibility", "Eligibility"),
    ("system", "System"),
    ("compliance", "Compliance"),
    ("evaluation", "Evaluation"),
    ("archive_short_name", "Archive Short Name"),
    ("acronym", "Acronym"),
    ("answer_type", "Answer Type"),
    ("mandatory", "Mandatory"),
    ("attachment", "Attachment"),
    ("compliance_attach", "Compliance"),     # the second "Compliance": an upload flag
    ("options", "Options"),
    ("option_scores", "Options Scores"),
    ("eval_options", "Eval Options"),
    ("eval_option_scores", "Eval Options Scores"),
    ("verify_against", "Verify Against"),
    ("gate_question", "Gate Question"),
    ("applies_to", "Applies To"),
]

# Rows whose Section cell carries these are structural, not questions.
STRUCTURAL_LABELS = {"sub total", "subtotal", "total marks", "total"}

# Client tokens stripped when normalising question text so that the same
# question across clients collapses to one bank entry.
CLIENT_TOKENS = ["ILRI", "ERCS", "SNV", "TASL", "Schindler", "TenderSure"]

# Client names that may appear inside question text and must be swapped for
# the target client when a question is reused. TenderSure is the platform,
# not a client, and is deliberately absent.
CLIENT_NAMES = ["ERCS", "SNV", "ILRI", "TASL", "Schindler"]

# Canonical section keys. Labels differ in suffix ("- ESG", "-TECH") and
# wording ("Director/ Shareholder Information" vs "Shareholder Information")
# across clients; the first matching keyword decides.
SECTION_KEYS = [
    ("declaration", "declaration"),
    ("company information", "company"),
    ("eligibility", "eligibility"),
    ("registration", "registration"),
    ("shareholder", "shareholder"),
    ("integrity", "integrity"),
    ("sustainab", "esg"),
    ("technical", "technical"),
    ("service level", "sla"),
    ("human resource", "hr"),
    ("experience", "experience"),
    ("trade reference", "trade_reference"),
    ("financial", "financial"),
    ("submission", "submission"),
]

# Sections the AI may never remove questions from: core to every sheet, and
# trade references carry marks. Rewording them is allowed. Enforced where
# removals are applied (review.apply_answers), not only where they are proposed.
PROTECTED_SECTIONS = {"trade_reference", "declaration", "submission"}

# A code that starts with one of these has lost its client prefix
# ("ET-2026_016" rather than "SNV-ET-2026_016"); the client is unknown.
COUNTRY_CODES = {"ET", "KE", "UG", "TZ", "RW", "SO", "SS"}

TOTAL_MARKS = 100

# Supplier business types: the options of the gate question (spec 02 G7) and
# the values of Applies To. Clients write them several ways.
BUSINESS_TYPES = ["Sole Proprietor", "Partnership", "Private Limited Company", "Share Company", "Individual Consultant"]
INDIVIDUAL = "Individual Consultant"
FIRMS = [b for b in BUSINESS_TYPES if b != INDIVIDUAL]
_BUSINESS_ALIASES = {
    "sole proprietor": "Sole Proprietor", "sole proprietorship": "Sole Proprietor",
    "private limited company": "Private Limited Company", "plc": "Private Limited Company",
    "share company": "Share Company",
    "individual consultant": INDIVIDUAL, "individual": INDIVIDUAL,
    "partnership": "Partnership", "public limited company": "Public Limited Company",
}


def business_types(text) -> list:
    """'sole proprietor,plc, Share company' -> the canonical names, in
    BUSINESS_TYPES order; unknown names are kept as written, at the end."""
    items = text if isinstance(text, (list, tuple)) else str(text or "").split(",")
    names = [_BUSINESS_ALIASES.get(" ".join(str(x).lower().split()), " ".join(str(x).split())) for x in items]
    names = [n for n in dict.fromkeys(names) if n]
    return sorted(names, key=lambda n: BUSINESS_TYPES.index(n) if n in BUSINESS_TYPES else len(BUSINESS_TYPES))
