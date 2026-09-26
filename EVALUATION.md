# AgentTrace — Evaluation Framework

This document describes the benchmark evaluation system for the AgentTrace multi-agent workflow.

---

## Quick Start

```bash
# Install dependencies
make dev

# Run full 50-task benchmark (stub mode, no API key needed)
make eval

# Run with a real LLM
export OPENAI_API_KEY=sk-...
make eval

# Run only tool-use tasks
python -m evals.runner --category tool_use --limit 10

# Generate comparison tables from all ablations
make experiment
make report
```

---

## Dataset

**Location:** `evals/datasets/benchmark_tasks.json`  
**Tasks:** 50 across 5 categories

| Category | Count | Description |
|----------|-------|-------------|
| `summarization` | 10 | Condensing, abstracting, constrained rewriting |
| `extraction` | 10 | Named entity, structured data, table parsing |
| `planning` | 10 | Step-by-step planning, roadmaps, coordination |
| `tool_use` | 10 | Calculator arithmetic, knowledge base retrieval |
| `error_recovery` | 10 | Ambiguous inputs, contradictions, out-of-scope |

### Task Schema

```json
{
  "task_id": "tool_001",
  "category": "tool_use",
  "difficulty": "easy",
  "description": "Human-readable description of what this task tests",
  "input": "The actual prompt sent to the agent",
  "expected_output": "Human-readable description of what a correct response looks like",
  "grading": {
    "method": "numeric_match",
    "expected_value": 67.5,
    "tolerance": 0.1
  },
  "requires_tools": ["calculator"]
}
```

### Adding New Tasks

1. Open `evals/datasets/benchmark_tasks.json`
2. Add a new object to the `tasks` array using the schema above
3. Choose a grading method (see below)
4. Run `make eval` to validate it works

---

## Grading Methods

All graders return `(success: bool, score: float 0-1, failure_reason: str)`.

### `keyword_contains`
Checks that the output contains at least `min_matches` of the specified `keywords` (case-insensitive substring match).

```json
{
  "method": "keyword_contains",
  "keywords": ["machine learning", "neural network", "model"],
  "min_matches": 2,
  "max_words_check": 100
}
```

### `numeric_match`
Extracts all numbers from the output and checks if any are within `tolerance` of `expected_value`.

```json
{
  "method": "numeric_match",
  "expected_value": 67.5,
  "tolerance": 0.1
}
```

### `format_check`
Verifies structural properties of the output (JSON validity, bullet/numbered lists, word count).

```json
{
  "method": "format_check",
  "checks": ["has_bullet_points", "min_words:20", "max_words:100"],
  "min_score": 0.6
}
```

Supported checks: `is_json`, `has_bullet_points`, `has_numbered_list`, `min_words:N`, `max_words:N`

### `rubric_score`
Scores based on multiple weighted criteria. Each criterion checks for keyword presence.

```json
{
  "method": "rubric_score",
  "criteria": [
    {"name": "mentions_cost", "keywords": ["cost", "price", "expensive"], "weight": 1.5},
    {"name": "mentions_tradeoff", "keywords": ["tradeoff", "versus", "however"], "weight": 1.0}
  ],
  "min_score": 0.5
}
```

### `plan_quality`
Specialized grader for planning tasks — checks for step-like structure plus keyword presence.

```json
{
  "method": "plan_quality",
  "keywords": ["deploy", "test", "monitor"],
  "min_score": 0.4
}
```

### `llm_judge`
Uses the LLM itself as a grader. Skipped (returns score=0) when no API key is present.

```json
{
  "method": "llm_judge",
  "rubric": "Is the response factually accurate, concise, and well-structured?"
}
```

---

## Output Format

Each eval run produces a JSONL file in `evals/results/` and a `_summary.json` companion.

### JSONL Row Schema

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | str | Agent run UUID (links to checkpoint) |
| `eval_run_id` | str | Batch evaluation run UUID |
| `timestamp` | str | ISO 8601 timestamp |
| `task_id` | str | Task identifier (e.g. `tool_001`) |
| `task_category` | str | Category (summarization, tool_use, …) |
| `difficulty` | str | easy / medium / hard |
| `input` | str | Prompt sent to the agent |
| `expected_output` | str | Human description of expected output |
| `actual_output` | str | Agent's final review output |
| `success` | bool | Whether the grader passed |
| `score` | float | Continuous score 0.0–1.0 |
| `failure_reason` | str | Short failure category |
| `model_name` | str | LLM model used |
| `prompt_version` | str | Prompt variant label |
| `config_version` | str | Experiment config label |
| `total_tokens` | int | Total tokens consumed |
| `prompt_tokens` | int | Input tokens |
| `completion_tokens` | int | Output tokens |
| `estimated_cost_usd` | float | Estimated API cost |
| `end_to_end_latency_ms` | float | Wall-clock time for full agent run |
| `retry_count` | int | Retries used across all agent nodes |
| `fallback_used` | bool | Whether any fallback was triggered |
| `tool_calls_made` | int | Number of tool calls made |
| `tool_calls` | list | Detailed tool call records |
| `requires_tools` | list | Tools the task was designed to use |

---

## Running Evaluations

### CLI Options

```bash
python -m evals.runner \
  --dataset   evals/datasets/benchmark_tasks.json \
  --output    evals/results/my_run.jsonl \
  --limit     10 \
  --category  tool_use \
  --prompt-version v2 \
  --config    my_experiment \
  --retry     default \
  --checkpoint
```

### Makefile Targets

| Target | Command | Description |
|--------|---------|-------------|
| `make eval` | `python -m evals.runner` | Run full 50-task benchmark |
| `make experiment` | `python -m experiments.runner` | Run all ablation configs |
| `make report` | `python -m evals.summarize` | Print summary of latest run |

---

## Telemetry Integration

Every eval run injects these attributes onto OTEL spans (visible in Azure Application Insights):

| Attribute | Value |
|-----------|-------|
| `eval.run_id` | Batch eval UUID |
| `eval.task_id` | Task identifier |
| `eval.task_category` | Category label |
| `eval.prompt_version` | e.g. `v1`, `v2` |
| `eval.model_name` | e.g. `gpt-4o-mini` |
| `eval.config_version` | Experiment config label |
| `eval.retry_count` | Retries used |
| `eval.fallback_used` | Boolean |
| `eval.final_status` | `success` / `failure` |
| `eval.failure_reason` | Short reason string |

This allows KQL queries in Application Insights to join evaluation outcomes with trace data:

```kusto
dependencies
| where timestamp > ago(1h)
| where customDimensions["eval.run_id"] != ""
| extend
    task_id    = tostring(customDimensions["eval.task_id"]),
    category   = tostring(customDimensions["eval.task_category"]),
    model      = tostring(customDimensions["eval.model_name"]),
    retry_ct   = toint(customDimensions["eval.retry_count"])
| summarize
    avg_duration = avg(duration),
    total_retries = sum(retry_ct)
  by task_id, category, model
| order by avg_duration desc
```
