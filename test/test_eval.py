"""Integration tests for the eval runner and grader."""

import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ["OTEL_INMEMORY_EXPORTER"] = "1"
# Remove all LLM keys so we get stub responses
for k in ["AZURE_OPENAI_API_KEY", "OPENAI_API_KEY", "GITHUB_MODELS_API_KEY"]:
    os.environ.pop(k, None)

from evals.grader import grade_task
from evals.summarize import generate_markdown_table, summarize_results


# ── Grader tests ──────────────────────────────────────────────────────────────

class TestGradeTask:
    def _task(self, method, **grading_kwargs):
        return {"grading": {"method": method, **grading_kwargs}}

    def test_stub_response_fails(self):
        task = self._task("keyword_contains", keywords=["test"])
        success, score, reason = grade_task(task, "[stub-response] something")
        assert not success
        assert score == 0.0
        assert "stub" in reason

    def test_error_response_fails(self):
        task = self._task("keyword_contains", keywords=["test"])
        success, score, reason = grade_task(task, "[llm-error-fallback] error")
        assert not success

    def test_empty_output_fails(self):
        task = self._task("keyword_contains", keywords=["test"])
        success, score, reason = grade_task(task, "")
        assert not success

    # keyword_contains
    def test_keyword_contains_pass(self):
        task = self._task("keyword_contains", keywords=["python", "code"], min_matches=1)
        success, score, reason = grade_task(task, "This is Python code for testing")
        assert success
        assert score > 0

    def test_keyword_contains_fail(self):
        task = self._task("keyword_contains", keywords=["java", "spring"], min_matches=2)
        success, score, reason = grade_task(task, "This is Python code")
        assert not success
        assert "keyword_mismatch" in reason

    def test_keyword_contains_case_insensitive(self):
        task = self._task("keyword_contains", keywords=["Python"], min_matches=1)
        success, score, reason = grade_task(task, "python is great")
        assert success

    def test_keyword_partial_match_scores_partial(self):
        # Use distinctive multi-char keywords that won't accidentally substring-match
        task = self._task(
            "keyword_contains",
            keywords=["python", "java", "golang", "rust"],
            min_matches=3,
        )
        success, score, reason = grade_task(task, "python is great")
        assert not success  # only 1 of 4 matched; need 3
        assert 0 < score < 1

    # numeric_match
    def test_numeric_match_exact(self):
        task = self._task("numeric_match", expected_value=67.5, tolerance=0.1)
        success, score, reason = grade_task(task, "The answer is 67.5")
        assert success
        assert score == 1.0

    def test_numeric_match_within_tolerance(self):
        task = self._task("numeric_match", expected_value=100.0, tolerance=1.0)
        success, score, reason = grade_task(task, "Result: 100.4")
        assert success

    def test_numeric_match_fail(self):
        task = self._task("numeric_match", expected_value=42.0, tolerance=0.5)
        success, score, reason = grade_task(task, "The answer is 99")
        assert not success

    def test_numeric_match_no_numbers(self):
        task = self._task("numeric_match", expected_value=42.0)
        success, score, reason = grade_task(task, "No numbers here at all")
        assert not success
        assert "no_number_found" in reason

    def test_numeric_match_comma_separated(self):
        task = self._task("numeric_match", expected_value=3000.0, tolerance=1.0)
        success, score, reason = grade_task(task, "Monthly cost: $3,000")
        assert success

    # format_check
    def test_format_check_bullet_points(self):
        task = self._task("format_check", checks=["has_bullet_points"], min_score=0.5)
        success, score, reason = grade_task(task, "- item one\n- item two\n- item three")
        assert success

    def test_format_check_numbered_list(self):
        task = self._task("format_check", checks=["has_numbered_list"], min_score=0.5)
        success, score, reason = grade_task(task, "1. First\n2. Second\n3. Third")
        assert success

    def test_format_check_min_words(self):
        task = self._task("format_check", checks=["min_words:5"], min_score=0.5)
        success, score, reason = grade_task(task, "one two three four five six")
        assert success

    def test_format_check_max_words_fail(self):
        task = self._task("format_check", checks=["max_words:3"], min_score=0.5)
        success, score, reason = grade_task(task, "one two three four five six seven")
        assert not success

    def test_format_check_json(self):
        task = self._task("format_check", checks=["is_json"], min_score=0.5)
        success, score, reason = grade_task(task, '{"key": "value"}')
        assert success

    def test_format_check_invalid_json(self):
        task = self._task("format_check", checks=["is_json"], min_score=0.5)
        success, score, reason = grade_task(task, "not json at all")
        assert not success

    # rubric_score
    def test_rubric_score_all_criteria_met(self):
        task = self._task(
            "rubric_score",
            criteria=[
                {"name": "c1", "keywords": ["python"], "weight": 1.0},
                {"name": "c2", "keywords": ["code"], "weight": 1.0},
            ],
            min_score=0.5,
        )
        success, score, reason = grade_task(task, "Python code is great")
        assert success
        assert score == 1.0

    def test_rubric_score_partial(self):
        task = self._task(
            "rubric_score",
            criteria=[
                {"name": "c1", "keywords": ["python"], "weight": 1.0},
                {"name": "c2", "keywords": ["java"], "weight": 1.0},
            ],
            min_score=0.4,
        )
        success, score, reason = grade_task(task, "Python is awesome")
        assert success  # 0.5 >= 0.4
        assert abs(score - 0.5) < 0.01

    # plan_quality
    def test_plan_quality_detects_steps(self):
        task = self._task("plan_quality", keywords=["deploy", "test"], min_score=0.3)
        success, score, reason = grade_task(
            task, "Step 1: Run tests\nStep 2: Deploy to production"
        )
        assert success

    def test_plan_quality_no_structure_low_score(self):
        task = self._task("plan_quality", keywords=["deploy", "test"], min_score=0.6)
        success, score, reason = grade_task(task, "Just do stuff and see what happens.")
        assert not success


# ── Summarize tests ───────────────────────────────────────────────────────────

class TestSummarize:
    def _make_rows(self):
        return [
            {
                "success": True,
                "score": 0.9,
                "end_to_end_latency_ms": 1200,
                "estimated_cost_usd": 0.0005,
                "total_tokens": 500,
                "task_category": "summarization",
                "failure_reason": "",
                "tool_calls_made": 0,
            },
            {
                "success": False,
                "score": 0.3,
                "end_to_end_latency_ms": 800,
                "estimated_cost_usd": 0.0002,
                "total_tokens": 300,
                "task_category": "extraction",
                "failure_reason": "keyword_mismatch",
                "tool_calls_made": 1,
            },
            {
                "success": True,
                "score": 0.8,
                "end_to_end_latency_ms": 1500,
                "estimated_cost_usd": 0.0006,
                "total_tokens": 600,
                "task_category": "summarization",
                "failure_reason": "",
                "tool_calls_made": 0,
            },
        ]

    def test_success_rate(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        assert summary["total_tasks"] == 3
        assert summary["success_count"] == 2
        assert abs(summary["success_rate_pct"] - 66.7) < 0.2

    def test_avg_score(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        expected = (0.9 + 0.3 + 0.8) / 3
        assert abs(summary["avg_score"] - expected) < 0.001

    def test_latency_percentiles(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        assert summary["p50_latency_ms"] > 0
        assert summary["p95_latency_ms"] >= summary["p50_latency_ms"]

    def test_total_cost(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        assert abs(summary["total_cost_usd"] - 0.0013) < 0.0001

    def test_category_breakdown(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        cats = summary["category_success_rate"]
        assert "summarization" in cats
        assert "extraction" in cats
        assert cats["summarization"]["success_rate"] == 100.0

    def test_failure_breakdown(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        assert "keyword_mismatch" in summary["failure_breakdown"]

    def test_tool_usage_stats(self):
        rows = self._make_rows()
        summary = summarize_results(rows)
        assert summary["tool_usage"]["tasks_using_tools"] == 1
        assert summary["tool_usage"]["total_tool_calls"] == 1

    def test_empty_rows(self):
        summary = summarize_results([])
        assert "error" in summary

    def test_generate_markdown_table(self):
        rows = self._make_rows()
        s1 = summarize_results(rows[:2])
        s2 = summarize_results(rows)
        md = generate_markdown_table([s1, s2], ["variant_a", "variant_b"])
        assert "variant_a" in md
        assert "variant_b" in md
        assert "Success Rate" in md
