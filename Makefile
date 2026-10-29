# One entry point for every party. Python runs in .venv at the root.
PY ?= .venv/bin/python

.PHONY: install test imports

install:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -e "packages/core[test]" -e "apps/cli[test]" -e "apps/api[test]" "import-linter>=2.1"

# every party's tests, then who may import whom (spec 06 M1)
test:
	$(PY) -m pytest
	$(MAKE) imports

imports:
	$(dir $(PY))lint-imports
