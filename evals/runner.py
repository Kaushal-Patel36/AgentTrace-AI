"""Benchmark runner for the LangGraph multi-agent evaluation framework.

Usage (CLI):
  python -m evals.runner --dataset evals/datasets/benchmark_tasks.json \\
                          --output  evals/results/run_<timestamp>.jsonl \\
                          [--limit  N]  [--category summarization]

Usage (import):
  from evals.runner import BenchmarkRunner
  runner = BenchmarkRunner()
  output_path = runner.run("evals/datasets/benchmark_tasks.json")

Each row in the output JSONL matches the full schema:
  run_id, timestamp, task_id, task_category, input, expected_output,
  actual_output, success, score, failure_reason, model_name,
  prompt_version, config_version, total_tokens, prompt_tokens,
  completion_tokens, estimated_cost_usd, end_to_end_latency_ms,
  retry_count, fallback_used, tool_calls_made, per_step_latency_ms
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path when run as a script
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.grader import grade_task
from evals.summarize import print_summary, summarize_results
from src.agent.agents import set_checkpoint_enabled, set_eval_context, set_retry_policy
from src.agent.graph import build_graph
from src.agent.instrumentation import get_in_memory_spans, reset_in_memory_spans
from src.agent.retry import RetryPolicy

# Per-token cost estimates (update to match current pricing)
_COST_PER_1K: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini":      {"input": 0.00015, "output": 0.0006},
    "gpt-4o":           {"input": 0.005,   "output": 0.015},
    "gpt-3.5-turbo":    {"input": 0.0005,  "output": 0.0015},
    "default":          {"input": 0.001,   "output": 0.002},
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = _COST_PER_1K.get(model, _COST_PER_1K["default"])
    return (
        prompt_tokens / 1000 * pricing["input"]
        + completion_tokens / 1000 * pricing["output"]
    )


def _extract_token_counts(spans: List[Any]) -> Dict[str, int]:
    """Pull token counts from in-memory OTEL spans captured during a run."""
    prompt_tokens = 0
    completion_tokens = 0
    for span in spans:
        attrs = dict(span.attributes or {})
        prompt_tokens += int(attrs.get("gen_ai.usage.input_tokens", 0))
        completion_tokens += int(attrs.get("gen_ai.usage.output_tokens", 0))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


class BenchmarkRunner:
    """Runs the full benchmark evaluation suite against the agent graph.

    Parameters:
        retry_policy:        RetryPolicy applied during the run (default: disabled for fast evals).
        prompt_version:      Label for the prompt variant being tested.
        config_version:      Label for this experiment configuration.
        checkpoint_enabled:  Whether to persist checkpoints during eval (default: False for speed).
        results_dir:         Directory to write JSONL results.
    """

    def __init__(
        self,
        retry_policy: Optional[RetryPolicy] = None,
        prompt_version: str = "v1",
        config_version: str = "default",
        checkpoint_enabled: bool = False,
        results_dir: Path = Path("evals/results"),
    ) -> None:
        self.retry_policy = retry_policy or RetryPolicy.disabled()
        self.prompt_version = prompt_version
        self.config_version = config_version
        self.checkpoint_enabled = checkpoint_enabled
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._graph = build_graph()

    def load_dataset(self, dataset_path: str) -> List[Dict[str, Any]]:
        """Load benchmark tasks from a JSON file."""
        path = Path(dataset_path)
        if not path.exists():
            raise FileNotFoundError(f"Dataset not found: {path}")
        with open(path) as f:
            data = json.load(f)
        tasks = data if isinstance(data, list) else data.get("tasks", [])
        return tasks

    def run(
        self,
        dataset_path: str = "evals/datasets/benchmark_tasks.json",
        output_path: Optional[str] = None,
        limit: Optional[int] = None,
        category_filter: Optional[str] = None,
        eval_run_id: Optional[str] = None,
    ) -> str:
        """Execute the full benchmark and save results as JSONL.

        Returns:
            Path to the written JSONL results file.
        """
        eval_run_id = eval_run_id or str(uuid.uuid4())
        tasks = self.load_dataset(dataset_path)

        # Filter by category if requested
        if category_filter:
            tasks = [t for t in tasks if t.get("category") == category_filter]

        # Apply limit
        if limit:
            tasks = tasks[:limit]

        # Resolve output path
        if not output_path:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            output_path = str(self.results_dir / f"run_{ts}.jsonl")

        # Configure agent modules for this eval run
        set_retry_policy(self.retry_policy)
        set_checkpoint_enabled(self.checkpoint_enabled)

        model_name = (
            os.getenv("AZURE_OPENAI_DEPLOYMENT")
            or os.getenv("MODEL_NAME", "stub")
        )

        # Enable in-memory span collection for token counting
        os.environ["OTEL_INMEMORY_EXPORTER"] = "1"

        results: List[Dict[str, Any]] = []
        print(f"\n🔬 Eval run {eval_run_id}")
        print(f"   Dataset  : {dataset_path} ({len(tasks)} tasks)")
        print(f"   Output   : {output_path}")
        print(f"   Model    : {model_name}")
        print(f"   Policy   : {self.retry_policy}")
        print()

        for i, task in enumerate(tasks, 1):
            task_id = task.get("task_id", f"task_{i:03d}")
            category = task.get("category", "unknown")
            task_input = task.get("input", "")

            print(f"  [{i:03d}/{len(tasks)}] {task_id} ({category}) ... ", end="", flush=True)

            set_eval_context({
                "eval_run_id": eval_run_id,
                "task_id": task_id,
                "prompt_version": self.prompt_version,
                "config_version": self.config_version,
            })

            reset_in_memory_spans()
            start = time.time()
            actual_output = ""
            run_result: Dict[str, Any] = {}
            error_msg = ""

            try:
                state = {"task": task_input, "eval_context": {"eval_run_id": eval_run_id, "task_id": task_id}}
                run_result = self._graph.invoke(state)
                # Use review as primary output; fall back to work
                actual_output = (
                    run_result.get("review")
                    or run_result.get("work")
                    or ""
                )
            except Exception as e:  # noqa: BLE001
                error_msg = str(e)
                actual_output = f"[runner-error] {e}"

            end_to_end_ms = round((time.time() - start) * 1000, 1)

            # Collect token counts from spans
            spans = get_in_memory_spans()
            tokens = _extract_token_counts(spans)

            # Grade the output
            success, score, failure_reason = grade_task(task, actual_output)
            if error_msg and not failure_reason:
                failure_reason = f"runner_exception:{error_msg[:120]}"

            cost_usd = _estimate_cost(
                model_name,
                tokens["prompt_tokens"],
                tokens["completion_tokens"],
            )

            row = {
                "run_id": run_result.get("run_id", ""),
                "eval_run_id": eval_run_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "task_category": category,
                "difficulty": task.get("difficulty", "medium"),
                "input": task_input,
                "expected_output": task.get("expected_output", ""),
                "actual_output": actual_output,
                "success": success,
                "score": round(score, 4),
                "failure_reason": failure_reason,
                "model_name": model_name,
                "prompt_version": self.prompt_version,
                "config_version": self.config_version,
                "total_tokens": tokens["total_tokens"],
                "prompt_tokens": tokens["prompt_tokens"],
                "completion_tokens": tokens["completion_tokens"],
                "estimated_cost_usd": round(cost_usd, 8),
                "end_to_end_latency_ms": end_to_end_ms,
                "retry_count": run_result.get("retry_count", 0),
                "fallback_used": run_result.get("fallback_used", False),
                "tool_calls_made": len(run_result.get("tool_calls") or []),
                "tool_calls": run_result.get("tool_calls") or [],
                "requires_tools": task.get("requires_tools", []),
            }
            results.append(row)

            status = "✓" if success else "✗"
            print(f"{status}  score={score:.2f}  {end_to_end_ms:.0f}ms")

        # Write JSONL
        with open(output_path, "w") as f:
            for row in results:
                f.write(json.dumps(row) + "\n")

        # Print summary
        summary = summarize_results(results)
        print_summary(summary, eval_run_id=eval_run_id)

        # Write summary JSON alongside JSONL
        summary_path = output_path.replace(".jsonl", "_summary.json")
        with open(summary_path, "w") as f:
            json.dump({"eval_run_id": eval_run_id, **summary}, f, indent=2)

        return output_path


# ── CLI entry point ───────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Run the LangGraph multi-agent benchmark evaluation"
    )
    parser.add_argument(
        "--dataset",
        default="evals/datasets/benchmark_tasks.json",
        help="Path to benchmark tasks JSON file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSONL path (auto-generated if omitted)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of tasks to run",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Filter to a specific task category",
    )
    parser.add_argument(
        "--prompt-version",
        default="v1",
        help="Prompt version label (used in results metadata)",
    )
    parser.add_argument(
        "--config",
        default="default",
        help="Config version label (used in results metadata)",
    )
    parser.add_argument(
        "--retry",
        choices=["disabled", "default", "aggressive"],
        default="disabled",
        help="Retry policy to apply",
    )
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="Enable SQLite checkpointing during eval",
    )
    args = parser.parse_args()

    policy_map = {
        "disabled": RetryPolicy.disabled(),
        "default": RetryPolicy.default(),
        "aggressive": RetryPolicy.aggressive(),
    }

    runner = BenchmarkRunner(
        retry_policy=policy_map[args.retry],
        prompt_version=args.prompt_version,
        config_version=args.config,
        checkpoint_enabled=args.checkpoint,
    )
    runner.run(
        dataset_path=args.dataset,
        output_path=args.output,
        limit=args.limit,
        category_filter=args.category,
    )


if __name__ == "__main__":
    _cli()
