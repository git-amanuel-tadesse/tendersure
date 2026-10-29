# One entry point for every party. Python runs in .venv at the root; JavaScript through pnpm.
PY ?= .venv/bin/python
BIN = $(dir $(PY))
CONTRACT = packages/api-client/openapi.json
TYPES = packages/api-client/src/schema.d.ts
GEN_TYPES = pnpm --filter @tendersure/api-client exec openapi-typescript

.PHONY: install dev build serve contract test imports contract-check web-test

install:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -e "packages/core[test]" -e "apps/cli[test]" -e "apps/api[test]" "import-linter>=2.1"
	pnpm install

# the API on :8765 and the web app on http://127.0.0.1:5173/ (open that one); Ctrl-C stops both
dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(BIN)tendersure-api --no-browser --debug & \
	pnpm --filter @tendersure/web dev

# the web app into apps/web/dist, which tendersure-api serves at http://127.0.0.1:8765/
build:
	pnpm --filter @tendersure/web build

serve: build
	$(BIN)tendersure-api

# after changing the API: the OpenAPI document and the web app's types, both committed
contract:
	$(PY) -m tendersure_api.contract $(CONTRACT)
	pnpm --filter @tendersure/api-client generate

# every party's tests, who may import whom, and a contract that matches the API
test:
	$(PY) -m pytest
	$(MAKE) imports contract-check web-test

imports:
	$(BIN)lint-imports
	node scripts/check-web-imports.mjs

contract-check:
	@tmp=$$(mktemp -d); \
	$(PY) -m tendersure_api.contract $$tmp/openapi.json && \
	$(GEN_TYPES) $$tmp/openapi.json --default-non-nullable false -o $$tmp/schema.d.ts >/dev/null && \
	if cmp -s $$tmp/openapi.json $(CONTRACT) && cmp -s $$tmp/schema.d.ts $(TYPES); then \
	  echo "contract: up to date"; rm -rf $$tmp; \
	else \
	  echo "contract: the API changed but $(CONTRACT) / schema.d.ts did not - run: make contract"; rm -rf $$tmp; exit 1; \
	fi

web-test:
	pnpm -r typecheck
	pnpm --filter @tendersure/web test
