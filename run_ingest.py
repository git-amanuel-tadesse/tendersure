"""Shortcut for `python -m tendersure ingest <folder> --out reports`."""
import os
import sys

from tendersure.cli import main

folder = sys.argv[1] if len(sys.argv) > 1 else "sample file"
if not os.path.isdir(folder):
    sys.exit(f"usage: python run_ingest.py <folder of criteria workbooks>  ({folder!r} not found)")
main(["ingest", folder, "--out", "reports"])
