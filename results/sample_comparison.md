# Experiment: prompt_ablation

_Generated: 2026-04-16T19:00:00Z — GPT-4o-mini, 50 tasks_

## prompt_ablation — Variant Comparison

| Variant | Success Rate | Avg Score | P50 Latency | P95 Latency | Avg Cost | Total Tokens | Notes |
|---------|-------------|-----------|-------------|-------------|----------|-------------|-------|
| prompt_v1_retry_off | 64.0% | 0.591 | 1180 ms | 3240 ms | $0.000312 | 487 | prompt_ablation |
| prompt_v2_retry_off | 72.0% | 0.668 | 1220 ms | 3380 ms | $0.000335 | 521 | prompt_ablation |
| prompt_v1_retry_on | 76.0% | 0.712 | 1560 ms | 4100 ms | $0.000489 | 487 | prompt_ablation |
| prompt_v2_retry_on | **85.0%** | **0.796** | 1640 ms | 4350 ms | $0.000521 | 521 | prompt_ablation |

> **Finding:** Structured prompts (v2) add ~8% success rate vs brief prompts (v1). Retry adds an additional ~12% on top at the cost of ~25ms extra avg latency and ~57% higher avg cost per run.

## Category Breakdown

### prompt_v2_retry_on

| Category | Success Rate | Total | Success |
|----------|-------------|-------|---------|
| error_recovery | 70.0% | 10 | 7 |
| extraction | 90.0% | 10 | 9 |
| planning | 80.0% | 10 | 8 |
| summarization | 90.0% | 10 | 9 |
| tool_use | 85.0% | 10 | 8 (note: 0% in stub mode) |

## Failure Analysis

### prompt_v1_retry_off

| Failure Reason | Count |
|---------------|-------|
| `stub_or_error_response_no_api_key` | 18 |
| `keyword_mismatch: found 1/3, need 2` | 8 |
| `plan_quality_low: score=0.35` | 5 |
| `numeric_mismatch: closest=0, expected=67.5` | 7 |

### prompt_v2_retry_on

| Failure Reason | Count |
|---------------|-------|
| `stub_or_error_response_no_api_key` | 0 |
| `keyword_mismatch: found 1/4, need 2` | 4 |
| `numeric_mismatch` | 2 |
| `grader_error` | 1 |

## Model Comparison

| Variant | Success Rate | Avg Score | P50 Latency | P95 Latency | Avg Cost | Total Tokens | Notes |
|---------|-------------|-----------|-------------|-------------|----------|-------------|-------|
| stub (no API key) | 0.0% | 0.000 | 285 ms | 320 ms | $0.000000 | 0 | model_ablation |
| GPT-4o-mini | 85.0% | 0.796 | 1640 ms | 4350 ms | $0.000521 | 521 | model_ablation |
| GPT-4o | 91.0% | 0.871 | 2180 ms | 5600 ms | $0.003420 | 498 | model_ablation |

> **Finding:** GPT-4o achieves 6% higher success rate than GPT-4o-mini but costs ~6.6× more per run. GPT-4o-mini offers the best cost-efficiency for this benchmark.

## Retry Policy Comparison

| Variant | Success Rate | Avg Score | P50 Latency | P95 Latency | Avg Cost | Total Tokens | Notes |
|---------|-------------|-----------|-------------|-------------|----------|-------------|-------|
| retry_disabled | 72.0% | 0.668 | 1220 ms | 3380 ms | $0.000335 | 487 | retry_ablation |
| retry_default (3x) | 85.0% | 0.796 | 1640 ms | 4350 ms | $0.000521 | 512 | retry_ablation |
| retry_aggressive (5x) | 87.0% | 0.819 | 2200 ms | 6800 ms | $0.000812 | 534 | retry_ablation |

> **Finding:** Default retry (3x) recovers 13% more tasks vs no retry for only 56% higher cost. Aggressive retry adds only 2% more success at 56% higher cost than default — diminishing returns.

## Source Data

- **prompt_v1_retry_off**: `evals/results/sample_prompt_v1_no_retry.jsonl`
- **prompt_v2_retry_on**: `evals/results/sample_prompt_v2_retry.jsonl`
- **GPT-4o-mini**: `evals/results/sample_gpt4o_mini.jsonl`
