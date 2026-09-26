"""Experiment runner for ablation studies.

Compares multiple configurations (prompt variants, models, retry policies)
by running the full benchmark under each configuration and generating
structured comparison tables in both Markdown and CSV.

Usage:
  python -m experiments.runner --config experiments/configs/
  python -m experiments.runner --experiment prompt_ablation

Output:
  results/comparison_<timestamp>.md
  results/comparison_<timestamp>.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.runner import BenchmarkRunner
from evals.summarize import generate_markdown_table, load_results_from_jsonl, summarize_results
from src.agent.retry import RetryPolicy


def _load_yaml_or_json(path: Path) -> Any:
    if HAS_YAML and path.suffix in {".yaml", ".yml"}:
        with open(path) as f:
            return yaml.safe_load(f)
    with open(path) as f:
        return json.load(f)


class ExperimentRunner:
    """Runs ablation experiments and produces comparison tables.

    Each experiment is a list of variants. Each variant specifies:
      - label        : human-readable name for the comparison table row
      - prompt_version: prompt variant tag
      - config_version : config label
      - retry_policy  : "disabled" | "default" | "aggressive"
      - model_env     : optional env var overrides {MODEL_NAME: "..."}
      - dataset       : optional path override (default: evals/datasets/benchmark_tasks.json)
      - limit         : optional task limit per variant
    """

    _RETRY_MAP = {
        "disabled":   RetryPolicy.disabled,
        "default":    RetryPolicy.default,
        "aggressive": RetryPolicy.aggressive,
    }

    def __init__(
        self,
        results_dir: Path = Path("results"),
        eval_results_dir: Path = Path("evals/results"),
    ) -> None:
        self.results_dir = Path(results_dir)
        self.eval_results_dir = Path(eval_results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.eval_results_dir.mkdir(parents=True, exist_ok=True)

    def run_experiment(
        self,
        variants: List[Dict[str, Any]],
        experiment_name: str = "comparison",
        dataset_path: str = "evals/datasets/benchmark_tasks.json",
        limit: Optional[int] = None,
    ) -> str:
        """Run all variants and write comparison tables.

        Returns:
            Path to the generated markdown file.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        all_summaries: List[Dict[str, Any]] = []
        all_labels: List[str] = []
        all_jsonl_paths: List[str] = []

        print(f"\n🧪 Experiment: {experiment_name}")
        print(f"   Variants : {len(variants)}")
        print(f"   Dataset  : {dataset_path}")
        print()

        for i, variant in enumerate(variants, 1):
            label = variant.get("label", f"variant_{i}")
            print(f"▸ Running variant [{i}/{len(variants)}]: {label}")

            retry_key = variant.get("retry_policy", "disabled")
            retry_fn = self._RETRY_MAP.get(retry_key, RetryPolicy.disabled)
            policy = retry_fn()

            # Override env vars for this variant
            env_overrides = variant.get("model_env", {})
            original_envs: Dict[str, Optional[str]] = {}
            for k, v in env_overrides.items():
                original_envs[k] = os.environ.get(k)
                os.environ[k] = str(v)

            try:
                runner = BenchmarkRunner(
                    retry_policy=policy,
                    prompt_version=variant.get("prompt_version", "v1"),
                    config_version=variant.get("config_version", label),
                    checkpoint_enabled=False,
                    results_dir=self.eval_results_dir,
                )
                out_path = str(
                    self.eval_results_dir / f"exp_{experiment_name}_{label}_{ts}.jsonl"
                )
                actual_path = runner.run(
                    dataset_path=variant.get("dataset", dataset_path),
                    output_path=out_path,
                    limit=variant.get("limit", limit),
                )
                rows = load_results_from_jsonl(actual_path)
                summary = summarize_results(rows)
                summary["config_version"] = label
                all_summaries.append(summary)
                all_labels.append(label)
                all_jsonl_paths.append(actual_path)

            finally:
                # Restore environment
                for k, original in original_envs.items():
                    if original is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = original

        # Generate outputs
        md_path = self._write_markdown(
            all_summaries, all_labels, experiment_name, all_jsonl_paths, ts
        )
        self._write_csv(all_summaries, all_labels, experiment_name, ts)

        print(f"\n✅ Experiment '{experiment_name}' complete.")
        print(f"   Results: {md_path}")
        return md_path

    def _write_markdown(
        self,
        summaries: List[Dict],
        labels: List[str],
        name: str,
        jsonl_paths: List[str],
        ts: str,
    ) -> str:
        md_path = str(self.results_dir / f"comparison_{name}_{ts}.md")

        lines = [
            f"# Experiment: {name}",
            f"\n_Generated: {ts}_  \n",
            generate_markdown_table(summaries, labels, title=f"{name} — Variant Comparison"),
            "\n\n## Category Breakdown\n",
        ]

        for label, summary in zip(labels, summaries):
            cat_rates = summary.get("category_success_rate", {})
            if cat_rates:
                lines.append(f"\n### {label}\n")
                lines.append("| Category | Success Rate | Total | Success |")
                lines.append("|----------|-------------|-------|---------|")
                for cat, stats in sorted(cat_rates.items()):
                    lines.append(
                        f"| {cat} | {stats['success_rate']}% "
                        f"| {stats['total']} | {stats['success']} |"
                    )

        lines.append("\n\n## Failure Analysis\n")
        for label, summary in zip(labels, summaries):
            failures = summary.get("failure_breakdown", {})
            if failures:
                lines.append(f"\n### {label}\n")
                lines.append("| Failure Reason | Count |")
                lines.append("|---------------|-------|")
                for reason, count in failures.items():
                    lines.append(f"| `{reason}` | {count} |")

        lines.append("\n\n## Source Data\n")
        for label, path in zip(labels, jsonl_paths):
            lines.append(f"- **{label}**: `{path}`")

        with open(md_path, "w") as f:
            f.write("\n".join(lines))

        return md_path

    def _write_csv(
        self,
        summaries: List[Dict],
        labels: List[str],
        name: str,
        ts: str,
    ) -> str:
        csv_path = str(self.results_dir / f"comparison_{name}_{ts}.csv")
        fieldnames = [
            "variant", "success_rate_pct", "avg_score", "p50_latency_ms",
            "p95_latency_ms", "p99_latency_ms", "avg_cost_usd", "total_cost_usd",
            "avg_tokens", "total_tasks", "success_count",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for label, summary in zip(labels, summaries):
                writer.writerow({
                    "variant": label,
                    **{k: summary.get(k, "") for k in fieldnames[1:]},
                })
        return csv_path

    def load_variants_from_dir(self, config_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
        """Load all variant configs from a directory of YAML/JSON files.

        Returns a dict mapping experiment_name -> list of variants.
        """
        experiments: Dict[str, List[Dict]] = {}
        for fp in sorted(config_dir.glob("*.yaml")) + sorted(config_dir.glob("*.json")):
            try:
                data = _load_yaml_or_json(fp)
                exp_name = fp.stem
                variants = data if isinstance(data, list) else data.get("variants", [data])
                experiments[exp_name] = variants
                print(f"  Loaded config: {fp.name} ({len(variants)} variants)")
            except Exception as e:
                print(f"  Warning: could not load {fp}: {e}")
        return experiments


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Run LangGraph ablation experiments")
    parser.add_argument("--config-dir", default="experiments/configs", help="Config directory")
    parser.add_argument("--experiment", default=None, help="Run only this experiment name")
    parser.add_argument("--dataset", default="evals/datasets/benchmark_tasks.json")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    runner = ExperimentRunner()
    experiments = runner.load_variants_from_dir(Path(args.config_dir))

    if not experiments:
        print("No experiment configs found. Creating default prompt_ablation experiment.")
        experiments = {
            "prompt_ablation": [
                {"label": "prompt_v1_no_retry", "prompt_version": "v1", "retry_policy": "disabled"},
                {"label": "prompt_v2_no_retry", "prompt_version": "v2", "retry_policy": "disabled"},
                {"label": "prompt_v1_retry", "prompt_version": "v1", "retry_policy": "default"},
            ]
        }

    for exp_name, variants in experiments.items():
        if args.experiment and exp_name != args.experiment:
            continue
        runner.run_experiment(
            variants=variants,
            experiment_name=exp_name,
            dataset_path=args.dataset,
            limit=args.limit,
        )


if __name__ == "__main__":
    _cli()
