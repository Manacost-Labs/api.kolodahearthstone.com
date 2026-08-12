PYTHON := .venv/bin/python
AI_QUALITY_BIN := /home/debian/server/tools/ai-quality/bin

.PHONY: setup check test provider-check lint-report security

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PYTHON) --requirement requirements-dev.txt

check:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q
	actionlint

test:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q

provider-check:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q \
		tests/test_firecrawl_scrape_do_fallback.py \
		tests/test_firecrawl_key_rotation.py

lint-report:
	ruff check --no-cache app tests scripts
	ruff format --check --no-cache app tests scripts

security:
	$(AI_QUALITY_BIN)/ai-security-check quick
