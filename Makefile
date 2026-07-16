# spica test harness — see tests/README.md for the full story.
#
# Assumes the `spica` pyenv virtualenv is active (or override PYTHON=...).
# The wand proxy is built from a sibling checkout (override WAND_DIR=...).

PYTHON     ?= python
PYTEST     ?= $(PYTHON) -m pytest

# Capture parameters (see `make capture`).
PROFILE ?= default
REGION  ?= us-east-1
VPC     ?=

.DEFAULT_GOAL := help

.PHONY: help install test test-bridge test-wand cov capture proxy clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install test/dev dependencies into the active environment
	$(PYTHON) -m pip install -r requirements-dev.txt

test: ## Run the whole suite (wand proxy tests skip if no proxy is up)
	$(PYTEST)

cov: ## Run the suite with a coverage report for src/
	$(PYTEST) --cov=src --cov-report=term-missing

clean: ## Remove test caches and the built proxy
	rm -rf .pytest_cache .coverage htmlcov coverage.xml .wand
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
