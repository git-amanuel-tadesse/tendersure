"""Synthetic criteria workbooks in the clients' layout, so tests need no client data."""

import os

import openpyxl

import tempfile

os.environ["TENDERSURE_NO_CACHE"] = "1"      # throwaway test workbooks must not fill .cache/
os.environ["TENDERSURE_OUTBOX"] = tempfile.mkdtemp(prefix="outbox-")   # nor the real audit log
import pytest

from tendersure.paths import home

HEADERS = ["Section", "Sub Section", "Criteria", "Marks", "System", "Compliance",
           "Archive Short Name", "Acronym", "Answer Type", "Mandatory", "Attachment",
           "Compliance", "Options", "Options Scores"]

# (section, sub, criteria, marks, system, compliance, archive, acronym, type, mand, L, M, options, scores)
GENERIC = [
    ("Declaration", "N/A", "I/We declare the information is true to {C}.", 0, None, None, None, None, 4, True, None, None, None, None),
    ("Company Information", "Information", "Company name", 0, None, None, "Company Name", None, 1, None, None, None, None, None),
    (None, None, "Organization type", 0, None, None, None, None, 2, None, None, None, "sole proprietor,plc", "0,0"),
    (None, None, "Attach evidence of physically registered office", 10, None, 10, "Office", None, 5, True, None, True, None, None),
    ("SUB", ),
    ("Registration and Statutory Requirements", "N/A", "Attach TIN certificate", 0, None, 0, "TIN", "TIN", 5, True, None, True, None, None),
    (None, None, "Insert TIN number", 0, None, None, None, None, 1, None, None, None, None, None),
    ("SUB", ),
]


def tech(kind):
    if kind == "goods":
        return [
            ("Technical Specifications-TECH", "N/A", "Do you offer extended warranty on goods sold Yes/No", 10, 10, None, None, None, 4, None, None, None, None, None),
            (None, None, "Are you a manufacturer or distributor? Attach authorisation", 10, None, 10, "DAF", "DAF", 5, None, None, True, None, None),
            ("SUB", ),
        ]
    return [
        ("Technical Specifications-TECH", "N/A", "Do you hold a valid service licence? Yes/No", 10, 10, None, None, None, 4, None, None, None, None, None),
        (None, None, "Attach evidence of trained service staff", 10, None, 10, "Staff", None, 5, None, None, True, None, None),
        ("SUB", ),
    ]


TAIL = [
    ("Business Experience- EXP", "N/A", "Years in business", 20, 20, None, None, None, 2, None, None, None, "0-1, 2 and Above", "0,20"),
    (None, None, "Is your company a current supplier of {C}?", 0, 0, None, None, None, 4, True, None, None, None, None),
    ("SUB", ),
    ("Trade Reference", "Ref 1", "Client Name", 0, None, None, None, None, 1, None, None, None, None, None),
    (None, None, "Attach evidence e.g. PO or award letter", 50, None, 50, "Reference 1", "REF 1", 5, True, None, True, None, None),
    ("SUB", ),
    ("Submission", "N/A", "I/We confirm the submission is complete.", 0, None, None, None, None, 4, None, None, None, None, None),
    ("TOTAL", ),
]


def write_sheet(ws, code, desc, rows, client):
    ws.cell(2, 2, f"{code}: {desc}")
    ws.cell(3, 5, "Marks")
    for i, h in enumerate(HEADERS):
        ws.cell(4, 2 + i, h)
    r = 5
    sums, every, subtotal_rows = [0, 0, 0], [0, 0, 0], []
    for row in rows:
        if row[0] in ("SUB", "TOTAL"):
            vals = sums if row[0] == "SUB" else every
            ws.cell(r, 2, "Sub Total" if row[0] == "SUB" else "Total Marks")
            for j, v in enumerate(vals):
                ws.cell(r, 5 + j, v)
            sums = [0, 0, 0]
            r += 1
            continue
        for j, v in enumerate(row):
            if isinstance(v, str):
                v = v.replace("{C}", client)
            ws.cell(r, 2 + j, v)
        for j, k in enumerate((3, 4, 5)):
            sums[j] += row[k] or 0
            every[j] += row[k] or 0
        r += 1


@pytest.fixture
def corpus(tmp_path):
    """Two clients, goods and service categories, as on-disk workbooks."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    specs = [
        ("AAA-ET-2026-001", "Supply of Office Stationery and Toners", "goods", "AAA"),
        ("AAA-ET-2026-002", "Supply of Electronics and Home Appliances", "goods", "AAA"),
        ("AAA-ET-2026-003", "Provision of Security and Guarding Services", "service", "AAA"),
    ]
    for code, desc, kind, client in specs:
        write_sheet(wb.create_sheet(code), code, desc, GENERIC + tech(kind) + TAIL, client)
    path = tmp_path / "AAA.xlsx"
    wb.save(path)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    write_sheet(wb.create_sheet("BBB-ET-001"), "BBB-ET-001", "Provision of Catering Services",
                GENERIC + [
                    ("Technical Specifications-TECH", "N/A", "Please attach your food safety and hygiene procedures", 10, None, 10, None, None, 5, None, None, True, None, None),
                    (None, None, "Do you use temperature regulated food containers Yes/No", 10, 10, None, None, None, 4, None, None, None, None, None),
                    ("SUB", ),
                ] + TAIL, "BBB")
    wb.save(tmp_path / "BBB.xlsx")
    return tmp_path


@pytest.fixture
def real_corpus():
    """Client workbooks: $TENDERSURE_CORPUS, else the project's 'sample file' folder."""
    here = os.environ.get("TENDERSURE_CORPUS") or os.path.join(home(), "sample file")
    if not os.path.isdir(here) or not any(f.endswith(".xlsx") for f in os.listdir(here)):
        pytest.skip("client sample workbooks not present")
    return here
