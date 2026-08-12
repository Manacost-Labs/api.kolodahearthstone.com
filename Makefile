PYTHON := .venv/bin/python
AI_QUALITY_BIN := /home/debian/server/tools/ai-quality/bin

.PHONY: setup check test panel-check platform-check provider-check docs-check sdk-check lint-report security benchmark-smoke

API_BENCHMARK_BASE_URL ?= http://127.0.0.1:8000

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PYTHON) --requirement requirements-dev.txt
	uv pip install --python $(PYTHON) --requirement panel/requirements.txt

check:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q
	$(MAKE) panel-check
	$(MAKE) platform-check
	$(MAKE) docs-check
	$(MAKE) sdk-check
	actionlint

test:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q

panel-check:
	$(PYTHON) -m unittest discover -s panel/tests -p 'test_*.py'
	@for test_file in panel/tests/*_test.php; do php "$$test_file"; done
	node --test panel/tests/*.test.js
	panel/tests/test_sync_locking.sh
	panel/tests/runtime_layout_test.sh
	@find panel -type f -name '*.php' -print0 | xargs -0 -n1 php -l >/dev/null

platform-check:
	PANEL_ROOT=$(CURDIR)/panel platform/tests/admin-ui-contract.sh
	platform/tests/migration-cutover-contract.sh
	php platform/tests/statistics-normalizer.test.php
	@find platform/scripts -type f -name '*.php' -print0 | xargs -0 -n1 php -l >/dev/null
	@find platform/scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

provider-check:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q \
		tests/test_firecrawl_scrape_do_fallback.py \
		tests/test_firecrawl_key_rotation.py

docs-check:
	scripts/check-documentation-links.sh
	$(PYTHON) scripts/check-readme-endpoints.py

sdk-check:
	npm --prefix sdks/typescript ci --ignore-scripts
	npm --prefix sdks/typescript run check
	npm --prefix sdks/typescript test
	npm --prefix sdks/typescript audit --audit-level=high
	@if command -v dotnet >/dev/null 2>&1; then \
		dotnet format sdks/dotnet/KolodaHearthstone.Api.SmokeTests/KolodaHearthstone.Api.SmokeTests.csproj --verify-no-changes; \
		dotnet build sdks/dotnet/KolodaHearthstone.Api.SmokeTests/KolodaHearthstone.Api.SmokeTests.csproj --configuration Release; \
		dotnet run --project sdks/dotnet/KolodaHearthstone.Api.SmokeTests/KolodaHearthstone.Api.SmokeTests.csproj --configuration Release --no-build; \
	else \
		printf 'dotnet is unavailable locally; the C# SDK gate runs in CI.\n'; \
	fi

lint-report:
	ruff check --no-cache app tests scripts
	ruff format --check --no-cache app tests scripts

security:
	$(AI_QUALITY_BIN)/ai-security-check quick

benchmark-smoke:
	$(PYTHON) scripts/benchmark_api.py --base-url $(API_BENCHMARK_BASE_URL) --requests 20 --concurrency 4
