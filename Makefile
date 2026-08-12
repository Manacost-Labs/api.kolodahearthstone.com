PYTHON := .venv/bin/python
AI_QUALITY_BIN := /home/debian/server/tools/ai-quality/bin

.PHONY: setup check test panel-check provider-check lint-report security

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PYTHON) --requirement requirements-dev.txt
	uv pip install --python $(PYTHON) --requirement panel/requirements.txt

check:
	@test -x $(PYTHON) || { printf 'Run make setup first.\n' >&2; exit 1; }
	$(PYTHON) -m pytest -q
	$(MAKE) panel-check
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
