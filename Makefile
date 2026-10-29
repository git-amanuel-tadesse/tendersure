# One entry point for every party. Python runs in .venv at the root.
PY ?= .venv/bin/python

.PHONY: install test

install:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -e "packages/core[test]"

test:
	$(PY) -m pytest
