"""The bank: historical criteria sheets in one local SQLite file.

`ingest` loads workbooks into the bank once; a workbook already in it (same
content) is skipped, and a workbook whose content changed at the same path
replaces its earlier version. The build reads only from the bank - never the
original folder - so the history can live anywhere, or be archived, once
ingested.

Each source workbook's bytes are kept too: the writer copies the client's own
styling (fonts, fills, widths, merged blocks) from the sheet a new one is
built from, so the bank alone is enough to produce a finished file.

Tables
  files       one row per workbook version (hash, name, path, bytes, when)
  sheets      one row per criteria sheet (client, code, description, year...)
  rows        the questions, one row each, all fields kept
  structural  subtotal / total rows

The bank is plain SQLite: inspect it with any SQLite tool. It holds client
data, so it is git-ignored.
"""

import hashlib
import io
import json
import os
import re
import sqlite3
import time
from dataclasses import asdict
from typing import Dict, List, Optional

import openpyxl

from .parser import Row, Sheet, StructuralRow, parse_workbook
from .paths import current

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    hash TEXT PRIMARY KEY, name TEXT, path TEXT, size INTEGER,
    ingested_at TEXT, superseded INTEGER DEFAULT 0, content BLOB);
CREATE TABLE IF NOT EXISTS sheets (
    name TEXT PRIMARY KEY, file_hash TEXT, source_file TEXT, title TEXT, code TEXT,
    description TEXT, client TEXT, year INTEGER, header_row INTEGER, n_columns INTEGER,
    misplaced_labels TEXT);
CREATE TABLE IF NOT EXISTS rows (
    sheet TEXT, idx INTEGER, section TEXT, criteria TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS structural (
    sheet TEXT, idx INTEGER, excel_row INTEGER, label TEXT,
    marks REAL, system REAL, compliance REAL);
CREATE INDEX IF NOT EXISTS rows_sheet ON rows(sheet);
CREATE INDEX IF NOT EXISTS sheets_client ON sheets(client);
"""


class BankStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path = path or current().bank
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self._templates: Dict[str, openpyxl.Workbook] = {}

    # --- writing ---------------------------------------------------------------

    def ingest_folder(self, folder: str, label: Optional[str] = None) -> Dict[str, int]:
        """Add every new or changed workbook under `folder` (subfolders too).

        A workbook is known by its path: new content at the same path replaces
        the old. `label` records paths as `<label>/<path inside folder>` instead
        of absolute ones, for a folder that is only a staging copy (an upload):
        uploading the same files again then replaces as a re-ingest does."""
        import glob
        files = sorted(f for f in glob.glob(os.path.join(folder, "**", "*.xlsx"), recursive=True)
                       if not os.path.basename(f).startswith("~$"))
        stats = {"files": len(files), "added": 0, "unchanged": 0, "replaced": 0, "sheets_added": 0}
        for path in files:
            with open(path, "rb") as fh:
                content = fh.read()
            h = hashlib.sha1(content).hexdigest()
            if self.db.execute("SELECT 1 FROM files WHERE hash=?", (h,)).fetchone():
                stats["unchanged"] += 1
                continue
            known_as = "/".join([label] + os.path.relpath(path, folder).split(os.sep)) if label \
                else os.path.abspath(path)
            old = self.db.execute("SELECT hash FROM files WHERE path=? AND superseded=0",
                                  (known_as,)).fetchall()
            for (oh,) in old:                       # same workbook, new content: replace it
                self._drop_file_sheets(oh)
                self.db.execute("UPDATE files SET superseded=1, content=NULL WHERE hash=?", (oh,))
                stats["replaced"] += 1
            sheets = parse_workbook(path)
            self.db.execute("INSERT INTO files VALUES (?,?,?,?,?,0,?)",
                            (h, os.path.basename(path), known_as, len(content),
                             time.strftime("%Y-%m-%d %H:%M:%S"), content))
            for s in sheets:
                self._insert_sheet(s, h)
            stats["added"] += 1
            stats["sheets_added"] += len(sheets)
        self.db.commit()
        return stats

    def _drop_file_sheets(self, file_hash: str):
        names = [n for (n,) in self.db.execute("SELECT name FROM sheets WHERE file_hash=?", (file_hash,))]
        for n in names:
            self.db.execute("DELETE FROM rows WHERE sheet=?", (n,))
            self.db.execute("DELETE FROM structural WHERE sheet=?", (n,))
        self.db.execute("DELETE FROM sheets WHERE file_hash=?", (file_hash,))

    def _insert_sheet(self, s: Sheet, file_hash: str):
        name = s.name
        if self.db.execute("SELECT 1 FROM sheets WHERE name=?", (name,)).fetchone():
            name = f"{s.name} [{os.path.splitext(s.source_file)[0]}]"   # same tab name, another workbook
        m = re.search(r"(20\d\d)", s.code or s.title or "")
        self.db.execute("INSERT INTO sheets VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (name, file_hash, s.source_file, s.title, s.code, s.description, s.client,
                         int(m.group(1)) if m else None, s.header_row, s.n_columns,
                         json.dumps(s.misplaced_labels)))
        self.db.executemany("INSERT INTO rows VALUES (?,?,?,?,?)",
                            [(name, i, r.section, r.criteria, json.dumps(asdict(r)))
                             for i, r in enumerate(s.rows)])
        self.db.executemany("INSERT INTO structural VALUES (?,?,?,?,?,?,?)",
                            [(name, i, x.excel_row, x.label, x.marks, x.system, x.compliance)
                             for i, x in enumerate(s.structural)])

    # --- reading ---------------------------------------------------------------

    def load_sheets(self, client: Optional[str] = None) -> List[Sheet]:
        q = ("SELECT name, file_hash, source_file, title, code, description, client, header_row, "
             "n_columns, misplaced_labels FROM sheets" + (" WHERE client=?" if client else "") +
             " ORDER BY source_file, rowid")
        out = []
        for (name, fh, src, title, code, desc, cl, hdr, ncol, mis) in \
                self.db.execute(q, (client,) if client else ()):
            s = Sheet(source_file=src, name=name, title=title, code=code, description=desc, client=cl,
                      header_row=hdr, n_columns=ncol, misplaced_labels=json.loads(mis or "[]"))
            s.source_hash = fh
            s.rows = [Row(**json.loads(d)) for (d,) in
                      self.db.execute("SELECT data FROM rows WHERE sheet=? ORDER BY idx", (name,))]
            s.structural = [StructuralRow(name, xr, lab, mk, sy, co) for (xr, lab, mk, sy, co) in
                            self.db.execute("SELECT excel_row, label, marks, system, compliance "
                                            "FROM structural WHERE sheet=? ORDER BY idx", (name,))]
            out.append(s)
        from .privacy import register_clients
        register_clients(s.client for s in out)      # every client is anonymised in model calls
        return out

    def template(self, sheet: Sheet) -> openpyxl.Workbook:
        """The workbook a sheet came from, for copying its styling."""
        h = getattr(sheet, "source_hash", None)
        if not h:
            return openpyxl.load_workbook(sheet.source_path)
        if h not in self._templates:
            row = self.db.execute("SELECT content FROM files WHERE hash=?", (h,)).fetchone()
            if not row or row[0] is None:
                raise FileNotFoundError(f"the bank has no workbook content for {sheet.name}")
            self._templates[h] = openpyxl.load_workbook(io.BytesIO(row[0]))
        return self._templates[h]

    def summary(self) -> Dict[str, int]:
        one = lambda q: self.db.execute(q).fetchone()[0]
        return {"files": one("SELECT COUNT(*) FROM files WHERE superseded=0"),
                "sheets": one("SELECT COUNT(*) FROM sheets"),
                "questions": one("SELECT COUNT(*) FROM rows"),
                "clients": one("SELECT COUNT(DISTINCT client) FROM sheets WHERE client IS NOT NULL")}

    def clients(self) -> List[str]:
        return [c for (c,) in self.db.execute(
            "SELECT DISTINCT client FROM sheets WHERE client IS NOT NULL ORDER BY client")]

    def last_ingest(self) -> Optional[str]:
        return self.db.execute("SELECT MAX(ingested_at) FROM files").fetchone()[0]
