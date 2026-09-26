# Developer workflow automation

PYTHON?=.venv/bin/python
PIP?=.venv/bin/pip

.PHONY: help install dev test coverage run fmt lint clean eval experiment report

help:
	@echo ""
	@echo "  LangGraph Multi-Agent — Developer Targets"
	@echo "  ─────────────────────────────────────────"
	@echo "  install     Install production deps"
	@echo "  dev         Editable install with dev extras (pytest, ruff, pyyaml)"
	@echo "  test        Run full test suite"
	@echo "  coverage    Run tests with coverage report"
	@echo "  lint        Ruff check (fails on errors)"
	@echo "  fmt         Ruff check (non-failing) + auto-format"
	@echo "  run         Start FastAPI app locally (uvicorn --reload)"
	@echo "  eval        Run 50-task benchmark evaluation"
	@echo "  experiment  Run all ablation experiments (prompt/model/retry)"
	@echo "  report      Print summary of latest eval run"
	@echo "  clean       Remove caches, coverage, and SQLite state db"
	@echo ""

install:
	$(PIP) install .

dev:
	$(PIP) install -e .[dev]

freeze:
	$(PIP) freeze > requirements.lock.txt

test:
	$(PYTHON) -m pytest

coverage:
	$(PYTHON) -m pytest --cov=src --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check .

fmt:
	$(PYTHON) -m ruff check . || true
	$(PYTHON) -m ruff format .

run:
	$(PYTHON) -m uvicorn src.agent.app:app --reload

# ── Evaluation targets ──────────────────────────────────────────────────────

eval:
	$(PYTHON) -m evals.runner \
		--dataset evals/datasets/benchmark_tasks.json \
		--prompt-version v1 \
		--config default

eval-tool-use:
	$(PYTHON) -m evals.runner \
		--dataset evals/datasets/benchmark_tasks.json \
		--category tool_use \
		--prompt-version v1

eval-quick:
	$(PYTHON) -m evals.runner \
		--dataset evals/datasets/benchmark_tasks.json \
		--limit 10

experiment:
	$(PYTHON) -m experiments.runner \
		--config-dir experiments/configs \
		--dataset evals/datasets/benchmark_tasks.json

report:
	@if ls evals/results/*.jsonl 1>/dev/null 2>&1; then \
		latest=$$(ls -t evals/results/*.jsonl | head -1); \
		echo "Latest results: $$latest"; \
		$(PYTHON) -c "\
from evals.summarize import load_results_from_jsonl, summarize_results, print_summary; \
rows = load_results_from_jsonl('$$latest'); \
summary = summarize_results(rows); \
print_summary(summary)"; \
	else \
		echo "No eval results found. Run: make eval"; \
	fi

# ── Cleanup ─────────────────────────────────────────────────────────────────

clean:
	rm -rf .pytest_cache .coverage htmlcov __pycache__
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	rm -f agent_runs.db
