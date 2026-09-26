# AgentTrace — Architecture

End-to-end architecture for the AgentTrace multi-agent observability platform.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT / EVAL RUNNER                     │
│  curl /run   │  python -m evals.runner   │  python -m exps     │
└──────┬───────┴────────────┬──────────────┴──────────┬──────────┘
       │                    │                          │
       ▼                    ▼                          ▼
┌──────────────┐   ┌────────────────┐   ┌────────────────────────┐
│  FastAPI App │   │ BenchmarkRunner│   │  ExperimentRunner      │
│  (uvicorn)   │   │ evals/runner.py│   │  experiments/runner.py │
│  app.py      │   └───────┬────────┘   └────────────┬───────────┘
└──────┬───────┘           │                         │
       │                   └──────────┬──────────────┘
       ▼                              ▼
┌───────────────────────── LangGraph Workflow ─────────────────────┐
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ Planner  │→ │  Worker  │→ │  Reflector   │→ │   Reviewer   │ │
│  │ Agent    │  │  Agent   │  │  Agent       │  │   Agent      │ │
│  └──────────┘  └──────┬───┘  └──────────────┘  └──────────────┘ │
│                        │                                          │
│               ┌────────▼─────────┐                               │
│               │   Tool Registry  │                               │
│               │  ┌────────────┐  │                               │
│               │  │ Calculator │  │                               │
│               │  │ Tool       │  │                               │
│               │  ├────────────┤  │                               │
│               │  │ Retrieval  │  │                               │
│               │  │ Tool       │  │                               │
│               │  └────────────┘  │                               │
│               └──────────────────┘                               │
│                                                                   │
│  Each node: @traced_span + RetryPolicy + Checkpoint save          │
└───────────────────────────────────────────────────────────────────┘
       │                          │
       ▼                          ▼
┌──────────────┐      ┌────────────────────────┐
│  SQLite      │      │  OpenTelemetry SDK     │
│  Checkpoint  │      │  TracerProvider        │
│  Store       │      │  MeterProvider         │
│  persistence │      │  BatchSpanProcessor    │
│  .py         │      └────────────┬───────────┘
└──────────────┘                   │
                                   ▼
                    ┌──────────────────────────┐
                    │  Azure Monitor Exporter  │
                    │  App Insights            │
                    │  Log Analytics Workspace │
                    └──────────────────────────┘
```

---

## Component Reference

### FastAPI Application (`src/agent/app.py`)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run` | POST | Execute full agent workflow |
| `/health` | GET | Liveness/readiness probe |
| `/eval/sample` | GET | Single sample eval task |
| `/experiments/latest` | GET | Latest comparison table |
| `/runs` | GET | List recent checkpoint runs |
| `/runs/{run_id}` | GET | Replay a specific run |

### Agent Nodes (`src/agent/agents.py`)

| Node | Input State Keys | Output State Keys | LLM Call |
|------|-----------------|-------------------|----------|
| `planner_agent` | `task` | `plan`, `run_id` | `llm.completion.planner` |
| `worker_agent` | `plan`, `task` | `work`, `tool_calls`, `tool_results` | `llm.completion.worker` |
| `reflection_agent` | `work`, `task` | `reflection` | `llm.completion.reflection` |
| `reviewer_agent` | `work`, `reflection` | `review` | `llm.completion.reviewer` |

### Tool Registry (`src/agent/tools.py`)

| Tool | Input | Output | Use Case |
|------|-------|--------|----------|
| `calculator` | `{"expression": "..."}` | `{"result": float}` | Math, financial calculations |
| `retrieval` | `{"query": "..."}` | `{"results": [...]}` | Knowledge base lookup |

Tools are detected from plan text via keyword matching and called before the LLM worker prompt. Tool results are injected into the LLM context.

### Retry & Fallback (`src/agent/retry.py`)

```
LLM call
    │
    ▼
call_with_retry(fn, policy)
    │
    ├─── Attempt 0 → success? → return result
    │
    ├─── Attempt 0 → stub/empty? → sleep(base * factor^0 ± jitter) → retry
    │
    ├─── Attempt 0 → exception? → sleep → retry (if retry_on_exception=True)
    │
    ├─── Attempt N (max_retries) → exhausted
    │       ├── Have stub result? → return it (best effort)
    │       └── No result? → raise RetryError
    │
    └── Span attributes: retry.count, retry.exhausted, retry.fallback_used
```

**Presets:**

| Policy | max_retries | base_delay | factor |
|--------|------------|-----------|--------|
| `disabled` | 0 | — | — |
| `default` | 3 | 1.0s | 2.0× |
| `aggressive` | 5 | 0.5s | 2.5× |

### Persistence (`src/agent/persistence.py`)

```
Agent Node (post-execution)
    │
    └→ CheckpointStore.save(Checkpoint(run_id, step, state, timestamp, metadata))
           │
           ├── SQLiteCheckpointStore (default local-first backend)
           │     └── agent_runs.db
           │
           └── InMemoryCheckpointStore (used in unit tests)

Replay flow:
  GET /runs/{run_id}
    └→ store.list_steps(run_id) + store.load(run_id, step) for each step
```

### Evaluation Framework (`evals/`)

```
benchmark_tasks.json (50 tasks)
    │
    ▼
BenchmarkRunner.run()
    │
    ├─ For each task:
    │     ├─ set_eval_context({eval_run_id, task_id, ...})
    │     ├─ reset_in_memory_spans()
    │     ├─ _graph.invoke({task: input})
    │     ├─ extract token counts from spans
    │     ├─ grade_task(task, actual_output) → (success, score, reason)
    │     └─ write JSONL row
    │
    └─ summarize_results(rows) → print + write _summary.json

Graders: keyword_contains · numeric_match · format_check
         rubric_score · plan_quality · llm_judge
```

### Experiment Runner (`experiments/`)

```
experiments/configs/*.yaml
    │
    ▼
ExperimentRunner.run_experiment(variants)
    │
    ├─ For each variant:
    │     ├─ override env vars (MODEL_NAME, etc.)
    │     ├─ BenchmarkRunner(retry_policy, prompt_version, config_version)
    │     ├─ runner.run(dataset)
    │     └─ summarize_results(jsonl_rows)
    │
    ├─ generate_markdown_table(summaries, labels)
    └─ write results/comparison_<name>_<ts>.md + .csv
```

---

## Data Flow: Single Request

```
POST /run {"task": "What is 15% of 450?"}
    │
    ▼
workflow.execution span
    │
    ├─ planner.agent span
    │     └─ llm.completion.planner span (gen_ai.* attributes)
    │
    ├─ worker.agent span
    │     ├─ tool.calculator span {"expression": "0.15 * 450"}
    │     └─ llm.completion.worker span (with tool results in context)
    │
    ├─ reflection.agent span
    │     └─ llm.completion.reflection span
    │
    └─ reviewer.agent span
          └─ llm.completion.reviewer span
                │
                ├─ gen_ai.usage.input_tokens: 312
                ├─ gen_ai.usage.output_tokens: 89
                ├─ gen_ai.request.model: gpt-4o-mini
                ├─ retry.count: 0
                └─ eval.task_id: tool_001 (if in eval mode)
```

All spans exported to Azure Monitor → Application Insights for KQL analysis.

---

## Directory Layout

```
llm-observability-otel/
├── src/agent/
│   ├── app.py            # FastAPI app + endpoints
│   ├── agents.py         # Planner/Worker/Reflector/Reviewer + retry + checkpoint
│   ├── graph.py          # LangGraph StateGraph + GraphState
│   ├── instrumentation.py# OTEL setup + set_eval_attributes()
│   ├── tools.py          # ToolRegistry + calculator + retrieval
│   ├── retry.py          # RetryPolicy + call_with_retry
│   └── persistence.py    # CheckpointStore + SQLiteCheckpointStore
│
├── evals/
│   ├── runner.py         # BenchmarkRunner (CLI + library)
│   ├── grader.py         # 6 grading methods
│   ├── summarize.py      # Stats + markdown tables
│   └── datasets/
│       └── benchmark_tasks.json  # 50 benchmark tasks
│
├── experiments/
│   ├── runner.py         # ExperimentRunner (ablations)
│   └── configs/
│       ├── prompt_variants.yaml
│       ├── model_variants.yaml
│       └── retry_policies.yaml
│
├── results/              # Markdown + CSV comparison tables
├── evals/results/        # JSONL eval outputs
│
├── test/
│   ├── test_api.py       # FastAPI endpoint + span assertions
│   ├── test_graph.py     # Full workflow invocation
│   ├── test_telemetry.py # Span decorator + error recording
│   ├── test_tools.py     # Tool correctness + safety
│   ├── test_retry.py     # RetryPolicy + call_with_retry
│   ├── test_eval.py      # Grader + summarize functions
│   └── test_persistence.py # Checkpoint store
│
├── infrastructure/       # Terraform: ACR, Container Apps, App Insights, Azure OpenAI
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── EVALUATION.md
├── RESULTS.md
└── ARCHITECTURE.md       ← this file
```

---

## Deployment Architecture (Azure)

```
Developer workstation
    └→ deploy-to-acr.sh
           ├─ docker build → push → ACR
           └─ terraform apply
                  ├─ Resource Group
                  ├─ Log Analytics Workspace
                  ├─ Application Insights
                  ├─ Azure Container Registry
                  ├─ Azure OpenAI (gpt-4o-mini deployment)
                  └─ Container Apps Environment
                         └─ Container App (FQDN: public)
                                env: APPINSIGHTS_CONNECTION_STRING
                                     AZURE_OPENAI_ENDPOINT
                                     AZURE_OPENAI_API_KEY
                                     AZURE_OPENAI_DEPLOYMENT
```
