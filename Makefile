PYTHON ?= python3.13
OUTPUT_DIR ?= quality/raw
MUTATION_TARGET ?= transport_matcher.results.validation.*_position*

.PHONY: quality-fast quality-test quality quality-mutation

quality-fast:
	$(PYTHON) quality/check.py
	$(PYTHON) -m ruff check src tests quality
	@echo "Full engine evidence: make quality PYTHON=$(PYTHON) OUTPUT_DIR=$(OUTPUT_DIR)"

quality-test:
	mkdir -p "$(OUTPUT_DIR)"
	$(PYTHON) -m pytest tests -q --cov --cov-branch --cov-report=term:skip-covered --cov-report=json:"$(OUTPUT_DIR)/engine-python-coverage.json" --cov-report=xml:"$(OUTPUT_DIR)/engine-python-coverage.xml" --junitxml="$(OUTPUT_DIR)/engine-tests.xml"

quality: quality-fast quality-test

quality-mutation:
	mkdir -p "$(OUTPUT_DIR)"
	$(PYTHON) quality/mutation_report.py --start '$(MUTATION_TARGET)' "$(OUTPUT_DIR)"
	$(PYTHON) -m mutmut run '$(MUTATION_TARGET)'
	$(PYTHON) quality/mutation_report.py '$(MUTATION_TARGET)' "$(OUTPUT_DIR)"
