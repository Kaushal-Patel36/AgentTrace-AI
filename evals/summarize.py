"""Summary statistics for benchmark evaluation results.

Computes:
  - Success rate
  - Average / P50 / P95 / P99 latency
  - Average and total cost
  - Average score
  - Failure category breakdown
  - Success rate by task category
  - Tool usage statistics
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List


def _percentile(values: List[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = max(0, int(len(sorted_v) * p / 100) - 1)
    return round(sorted_v[idx], 1)


def summarize_results(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate statistics from a list of eval result rows."""
    if not rows:
        return {"error": "no_results"}

    total = len(rows)
    successes = [r for r in rows if r.get("success")]
    failures = [r for r in rows if not r.get("success")]

    latencies = [r["end_to_end_latency_ms"] for r in rows if r.get("end_to_end_latency_ms")]
    scores = [r["score"] for r in rows if isinstance(r.get("score"), (int, float))]
    costs = [r["estimated_cost_usd"] for r in rows if r.get("estimated_cost_usd") is not None]
    tokens = [r["total_tokens"] for r in rows if r.get("total_tokens") is not None]

    # Failure breakdown
    failure_reasons = Counter(
        r.get("failure_reason", "unknown") for r in failures if r.get("failure_reason")
    )

    # Success rate by category
    category_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "success": 0})
    for r in rows:
        cat = r.get("task_category", "unknown")
        category_stats[cat]["total"] += 1
        if r.get("success"):
            category_stats[cat]["success"] += 1

    category_success_rate = {
        cat: {
            "success_rate": round(v["success"] / v["total"] * 100, 1) if v["total"] else 0,
            "total": v["total"],
            "success": v["success"],
        }
        for cat, v in category_stats.items()
    }

    # Tool stats
    tool_used_count = sum(1 for r in rows if r.get("tool_calls_made", 0) > 0)
    total_tool_calls = sum(r.get("tool_calls_made", 0) for r in rows)

    return {
        "total_tasks": total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate_pct": round(len(successes) / total * 100, 1) if total else 0,
        "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "p99_latency_ms": _percentile(latencies, 99),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "avg_cost_usd": round(sum(costs) / len(costs), 8) if costs else 0.0,
        "total_cost_usd": round(sum(costs), 6) if costs else 0.0,
        "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else 0,
        "total_tokens": sum(tokens),
        "failure_breakdown": dict(failure_reasons.most_common()),
        "category_success_rate": category_success_rate,
        "tool_usage": {
            "tasks_using_tools": tool_used_count,
            "total_tool_calls": total_tool_calls,
            "tool_use_rate_pct": round(tool_used_count / total * 100, 1) if total else 0,
        },
    }


def print_summary(summary: Dict[str, Any], eval_run_id: str = "") -> None:
    """Pretty-print a summary to stdout."""
    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  Eval Summary{' — ' + eval_run_id[:8] + '…' if eval_run_id else ''}")
    print(sep)
    print(f"  Tasks         : {summary.get('total_tasks', 0)}")
    print(f"  Success rate  : {summary.get('success_rate_pct', 0)}%  "
          f"({summary.get('success_count', 0)}/{summary.get('total_tasks', 0)})")
    print(f"  Avg score     : {summary.get('avg_score', 0):.4f}")
    print(f"  P50 latency   : {summary.get('p50_latency_ms', 0)} ms")
    print(f"  P95 latency   : {summary.get('p95_latency_ms', 0)} ms")
    print(f"  P99 latency   : {summary.get('p99_latency_ms', 0)} ms")
    print(f"  Avg cost      : ${summary.get('avg_cost_usd', 0):.6f}")
    print(f"  Total cost    : ${summary.get('total_cost_usd', 0):.4f}")
    print(f"  Total tokens  : {summary.get('total_tokens', 0)}")

    cat_rates = summary.get("category_success_rate", {})
    if cat_rates:
        print(f"\n  Success by category:")
        for cat, stats in sorted(cat_rates.items()):
            bar = "█" * int(stats["success_rate"] // 10) + "░" * (10 - int(stats["success_rate"] // 10))
            print(f"    {cat:<22} {bar}  {stats['success_rate']}% ({stats['success']}/{stats['total']})")

    failures = summary.get("failure_breakdown", {})
    if failures:
        print(f"\n  Top failure reasons:")
        for reason, count in list(failures.items())[:5]:
            print(f"    {reason:<40} {count}")

    tool_stats = summary.get("tool_usage", {})
    if tool_stats:
        print(f"\n  Tool usage:")
        print(f"    Tasks using tools  : {tool_stats.get('tasks_using_tools', 0)} "
              f"({tool_stats.get('tool_use_rate_pct', 0)}%)")
        print(f"    Total tool calls   : {tool_stats.get('total_tool_calls', 0)}")

    print(f"{sep}\n")


def generate_markdown_table(
    summaries: List[Dict[str, Any]],
    labels: List[str],
    title: str = "Experiment Comparison",
) -> str:
    """Generate a markdown comparison table from multiple summary dicts."""
    lines = [f"## {title}\n"]
    header = (
        "| Variant | Success Rate | Avg Score | P50 Latency | P95 Latency "
        "| Avg Cost | Total Tokens | Notes |"
    )
    separator = "|---------|-------------|-----------|-------------|-------------|----------|-------------|-------|"
    lines.append(header)
    lines.append(separator)

    for label, summary in zip(labels, summaries):
        row = (
            f"| {label} "
            f"| {summary.get('success_rate_pct', 0)}% "
            f"| {summary.get('avg_score', 0):.3f} "
            f"| {summary.get('p50_latency_ms', 0)} ms "
            f"| {summary.get('p95_latency_ms', 0)} ms "
            f"| ${summary.get('avg_cost_usd', 0):.6f} "
            f"| {summary.get('avg_tokens', 0):.0f} "
            f"| {summary.get('config_version', '')} |"
        )
        lines.append(row)

    return "\n".join(lines)


def load_results_from_jsonl(path: str) -> List[Dict[str, Any]]:
    """Load eval results rows from a JSONL file."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


__all__ = [
    "summarize_results",
    "print_summary",
    "generate_markdown_table",
    "load_results_from_jsonl",
]
