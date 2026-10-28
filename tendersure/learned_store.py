"""Where learned facts are kept: the glossary and the Yes/No verdicts.

They are client-derived data, kept apart from the code (not in git). A
storage driver is any object with

    read(name)                  -> list of rows (dicts of column -> text)
    write(name, columns, rows)

CsvStore, the default, keeps one CSV file per table in a folder (data/). A
database or cloud driver can replace it later without touching the code that
learns the facts or uses them.
"""

import csv
import os
from typing import Dict, List

GLOSSARY = "glossary"
YES_NO = "yesno_polarity"


class CsvStore:
    def __init__(self, folder: str = "data"):
        self.folder = folder

    def path(self, name: str) -> str:
        return os.path.join(self.folder, f"{name}.csv")

    def read(self, name: str) -> List[Dict[str, str]]:
        try:
            with open(self.path(name), newline="") as fh:
                return list(csv.DictReader(fh))
        except FileNotFoundError:
            return []

    def write(self, name: str, columns: List[str], rows: List[Dict[str, str]]):
        os.makedirs(self.folder or ".", exist_ok=True)
        with open(self.path(name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)


def resolve(target, default_name: str):
    """A store and table name from a driver, or from a CSV path
    ('data/glossary.csv' -> CsvStore('data'), 'glossary')."""
    if isinstance(target, str):
        folder, file = os.path.split(target)
        return CsvStore(folder), os.path.splitext(file)[0] or default_name
    return target, default_name
