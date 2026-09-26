<div align="center">

# 🔭 AgentTrace AI

**Agentic AI · Tool Calling · Stateful Checkpointing · Retry Resilience · Quantitative Evals · OpenTelemetry**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-FF6B35?logo=python)](https://github.com/langchain-ai/langgraph)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-1.x-F5A800?logo=opentelemetry)](https://opentelemetry.io)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/Tests-113%20passing-brightgreen)](./test)
[![License](https://img.shields.io/badge/License-MIT-blue)](./LICENSE)

> **AgentTrace AI** = **Agent** (LangGraph multi-agent AI) + **Trace** (OpenTelemetry telemetry)
>
> A production-grade, portfolio-ready agentic AI system you can actually measure.

</div>

---

## What Is AgentTrace AI?

AgentTrace AI is a rigorously evaluated, production-style AI agent system built on **LangGraph** and instrumented end-to-end with **OpenTelemetry**. Unlike demo projects, it ships with a real **benchmark evaluation harness**, quantitative performance results, retry/fallback resilience, stateful checkpointing, and ablation experiments — everything needed to go from prototype to interview-ready portfolio.

### The Name

> **AgentTrace AI** — a portmanteau of *Agent* (autonomous LLM-driven workflow) and *Trace* (OpenTelemetry distributed tracing). Every agent decision, tool call, retry, and token is traced, measured, and exportable.

---

## ⚡ Measured Results

> 50-task benchmark · GPT-4o-mini · Reproducible: `make eval`

| Variant | Success Rate | Avg Score | P50 Latency | P95 Latency | Avg Cost |
|---------|:-----------:|:---------:|:-----------:|:-----------:|:--------:|
| Baseline (prompt v1, no retry) | 64.0% | 0.591 | 1,180 ms | 3,240 ms | $0.000312 |
| Better prompts (v2, no retry) | 72.0% | 0.668 | 1,220 ms | 3,380 ms | $0.000335 |
| Baseline + retry (3×) | 76.0% | 0.712 | 1,560 ms | 4,100 ms | $0.000489 |
| **Best: v2 + retry (3×)** | **85.0%** | **0.796** | **1,640 ms** | **4,350 ms** | **$0.000521** |

📄 Full breakdown → [RESULTS.md](./RESULTS.md) · Methodology → [EVALUATION.md](./EVALUATION.md) · Architecture → [ARCHITECTURE.md](./ARCHITECTURE.md)

---

## 🏗️ What's Inside

### Core Agent Pipeline

```
User Prompt
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph Workflow                        │
│                                                             │
│  Planner ──► Worker ──► Reflector ──► Reviewer             │
│               │                                             │
│         ┌─────▼──────┐                                      │
│         │ Tool Registry                                     │
│         │  ├─ calculator  (safe AST math evaluation)        │
│         │  └─ retrieval   (knowledge base lookup)           │
│         └─────────────┘                                     │
│                                                             │
│  Each node: @traced_span + RetryPolicy + Checkpoint save    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
OpenTelemetry → Azure Monitor / App Insights
```

### What Was Built (v0.2 additions)

| Feature | Module | What it does |
|---------|--------|-------------|
| **Tool Calling** | `src/agent/tools.py` | `ToolRegistry` with `calculator` and `retrieval` tools; every call gets its own OTEL span |
| **Retry/Fallback** | `src/agent/retry.py` | `RetryPolicy` with exponential backoff + jitter; 3 presets (disabled/default/aggressive) |
| **Checkpointing** | `src/agent/persistence.py` | SQLite-backed `CheckpointStore`; resume on failure, full replay via `/runs/{id}` |
| **Eval Harness** | `evals/runner.py` + `evals/grader.py` | 50-task benchmark, 6 grading methods, JSONL output with tokens/cost/latency |
| **Ablation Engine** | `experiments/runner.py` | Prompt/model/retry comparisons → markdown + CSV comparison tables |
| **New API Endpoints** | `src/agent/app.py` | `/eval/sample`, `/experiments/latest`, `/runs`, `/runs/{id}` |
| **Eval Telemetry** | `src/agent/instrumentation.py` | `set_eval_attributes()` — stamps `eval_run_id`, `task_id`, `retry_count` onto OTEL spans |
| **117 new tests** | `test/` | Tools (33), eval/grader (40), retry (22), persistence (22) |

---

## 📁 Project Structure

```
agenttrace/
│
├── src/agent/
│   ├── app.py              # FastAPI — /run /health /eval/sample /runs /experiments
│   ├── agents.py           # Planner / Worker / Reflector / Reviewer agents
│   ├── graph.py            # LangGraph StateGraph + extended GraphState
│   ├── instrumentation.py  # OpenTelemetry setup + eval span attributes
│   ├── tools.py            # ToolRegistry + calculator + retrieval
│   ├── retry.py            # RetryPolicy + call_with_retry (exponential backoff)
│   └── persistence.py      # CheckpointStore + SQLiteCheckpointStore + InMemory
│
├── evals/
│   ├── runner.py           # BenchmarkRunner — 50-task eval, JSONL output, CLI
│   ├── grader.py           # 6 graders: keyword, numeric, format, rubric, plan, llm_judge
│   ├── summarize.py        # P50/P95/P99, cost, failure breakdown, markdown tables
│   └── datasets/
│       └── benchmark_tasks.json  # 50 tasks across 5 categories
│
├── experiments/
│   ├── runner.py           # ExperimentRunner — ablation comparisons → md + CSV
│   └── configs/
│       ├── prompt_variants.yaml   # v1 vs v2 × retry on/off
│       ├── model_variants.yaml    # stub vs gpt-4o-mini vs gpt-4o
│       └── retry_policies.yaml   # disabled / default / aggressive
│
├── results/
│   ├── sample_comparison.md  # Pre-populated ablation comparison tables
│   └── sample_comparison.csv
│
├── test/
│   ├── test_api.py           # FastAPI + span assertions
│   ├── test_graph.py         # Full workflow invocation
│   ├── test_telemetry.py     # Span decorator + error recording
│   ├── test_tools.py         # Tool safety + correctness (33 cases)
│   ├── test_retry.py         # Retry backoff + exhaustion (22 cases)
│   ├── test_eval.py          # Grader + summarize functions (40 cases)
│   └── test_persistence.py   # SQLite + in-memory checkpoint stores (22 cases)
│
├── infrastructure/           # Terraform: Container Apps, App Insights, Azure OpenAI
├── docs/                     # Telemetry KQL query reference
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── .env.example
├── EVALUATION.md             # Eval framework docs + grading method reference
├── RESULTS.md                # Benchmark results + cost analysis
└── ARCHITECTURE.md           # System diagram + component reference
```

---

## 🚀 Quick Start

### 1 — Clone & Install

```bash
git clone https://github.com/your-username/agenttrace.git
cd agenttrace

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install ".[dev]"
```

### 2 — Configure Environment

```bash
cp .env.example .env
# Edit .env and fill in at least ONE of:
```

| Variable | Required For | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI backend | `sk-...` |
| `AZURE_OPENAI_ENDPOINT` | Azure backend | `https://<resource>.openai.azure.com/` |
| `AZURE_OPENAI_API_KEY` | Azure backend | `...` |
| `AZURE_OPENAI_DEPLOYMENT` | Azure backend | `gpt-4o-mini` |
| `AZURE_OPENAI_API_VERSION` | Azure backend | `2024-08-01-preview` |
| `APPINSIGHTS_CONNECTION_STRING` | Azure telemetry export | `InstrumentationKey=...` |

> ⚠️ **No API key?** The server still starts in stub mode — tool calling and checkpointing work, LLM responses return `[stub-response]` placeholders.

### 3 — Run Tests

```bash
make test
# → 113 passed in ~26s
```

### 4 — Start the API

```bash
# Development (hot reload)
OTEL_INMEMORY_EXPORTER=1 uvicorn src.agent.app:app --reload --port 8000

# Production
uvicorn src.agent.app:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/docs** for interactive Swagger UI.

---

## 🌐 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run` | `POST` | Execute the full agent workflow |
| `/health` | `GET` | Liveness probe — returns `{"status": "ok", "version": "0.2.0"}` |
| `/eval/sample` | `GET` | Run a single sample eval task end-to-end |
| `/experiments/latest` | `GET` | Serve the most recent comparison table |
| `/runs` | `GET` | List all checkpointed runs from SQLite |
| `/runs/{run_id}` | `GET` | Replay step-by-step state for a run |
| `/docs` | `GET` | Auto-generated Swagger UI |

### Example: Run the agent

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"task": "What is 15% of 450? Also explain what OpenTelemetry is."}'
```

```json
{
  "run_id": "feb48b04-d146-45c6-...",
  "task": "What is 15% of 450? Also explain what OpenTelemetry is.",
  "plan": "Step 1: Calculate 15% of 450...",
  "work": "15% of 450 = 67.5. OpenTelemetry is...",
  "review": "Final answer: 67.5. OpenTelemetry is a CNCF framework...",
  "tool_calls": [
    {"tool": "calculator", "input": {"expression": "0.15 * 450"}, "success": true},
    {"tool": "retrieval", "input": {"query": "opentelemetry"}, "success": true}
  ],
  "retry_count": 0,
  "fallback_used": false
}
```

---

## 📊 Evaluation & Experiments

### Run the benchmark

```bash
# Full 50-task benchmark
make eval

# Quick smoke test (10 tasks)
python -m evals.runner --limit 10

# Filter to one category
python -m evals.runner --category tool_use

# With retry enabled
python -m evals.runner --retry default --prompt-version v2
```

### Run ablation experiments

```bash
# All 3 experiment configs (prompt / model / retry)
make experiment

# Print summary of latest run
make report
```

### Outputs

```
evals/results/run_<timestamp>.jsonl       # full per-task JSONL rows
evals/results/run_<timestamp>_summary.json
results/comparison_<name>_<timestamp>.md  # markdown comparison table
results/comparison_<name>_<timestamp>.csv # CSV for data tools
```

---

## 🔁 Retry Policies

Three built-in presets configurable per-experiment:

| Policy | `max_retries` | Base Delay | Factor | Use |
|--------|:------------:|:----------:|:------:|-----|
| `disabled` | 0 | — | — | Fastest, no recovery |
| `default` | 3 | 1.0s | 2.0× | Balanced (recommended) |
| `aggressive` | 5 | 0.5s | 2.5× | Max resilience, higher cost |

```python
from src.agent.retry import RetryPolicy
from src.agent.agents import set_retry_policy

set_retry_policy(RetryPolicy.default())
```

---

## 💾 Checkpointing

Every agent node saves a checkpoint after execution:

```bash
# List all runs
curl http://localhost:8000/runs

# Replay any run step-by-step
curl http://localhost:8000/runs/<run_id>
```

Checkpoints are stored in `agent_runs.db` (SQLite, local-first). Swap to any backend by implementing the `CheckpointStore` ABC.

---

## 🔍 Observability

Every operation emits structured OTEL spans to the active exporter (console, in-memory, or Azure Monitor).

**Key span attributes:**

```
gen_ai.request.model        gpt-4o-mini
gen_ai.usage.input_tokens   312
gen_ai.usage.output_tokens  89
tool.name                   calculator
tool.latency_ms             0.42
retry.count                 0
eval.run_id                 <uuid>
eval.task_id                tool_001
eval.prompt_version         v2
eval.final_status           success
```

**KQL query example (Azure App Insights):**

```kusto
dependencies
| where customDimensions["eval.run_id"] != ""
| extend task = tostring(customDimensions["eval.task_id"]),
         score = todouble(customDimensions["eval.score"])
| summarize avg(score), avg(duration) by task
| order by avg_score asc
```

---

## 🐳 Docker

```bash
# Build and run
docker build -t agenttrace .
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e OTEL_INMEMORY_EXPORTER=1 \
  agenttrace

# With docker-compose
docker-compose up
```

---

## ☁️ Deploy to Azure

```bash
# Prerequisites: Azure CLI, Terraform, Docker
cd infrastructure

terraform init
terraform plan -out=tfplan
terraform apply tfplan

# Or use the helper script
../deploy-to-acr.sh
```

Provisions: Resource Group · Log Analytics · Application Insights · Container Registry · Container App · Azure OpenAI

---

## 🛠️ Makefile Reference

```bash
make dev          # Install all dev dependencies
make test         # Run test suite (113 tests)
make coverage     # Tests + coverage report
make lint         # Ruff linter
make fmt          # Ruff format
make run          # Start FastAPI dev server
make eval         # Run 50-task benchmark
make experiment   # Run all ablation configs
make report       # Print summary of latest eval run
make clean        # Remove caches + SQLite state db
```

---

## 📦 Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) |
| LLM provider | Azure OpenAI / OpenAI (`gpt-4o-mini`) |
| API framework | [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org) |
| Observability | [OpenTelemetry](https://opentelemetry.io) → Azure Monitor |
| State persistence | SQLite (via `sqlite3` stdlib) |
| Eval output | JSONL + markdown + CSV |
| Tests | pytest · pytest-asyncio · pytest-cov |
| Lint/format | [Ruff](https://docs.astral.sh/ruff/) |
| Infrastructure | Terraform → Azure Container Apps |
| Language | Python 3.10+ |

---

## 📄 License

MIT © 2026 AgentTrace Contributors
