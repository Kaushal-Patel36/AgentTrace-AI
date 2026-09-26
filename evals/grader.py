"""Grading functions for benchmark task evaluation.

Grading methods (set in each task's 'grading.method' field):
  keyword_contains  — output must contain N of M specified keywords
  numeric_match     — output must contain a number within tolerance of expected
  format_check      — output must satisfy structural rules (JSON, list, length)
  rubric_score      — score 0–1 based on multiple weighted criteria
  plan_quality      — verify planning responses have step-like structure
  llm_judge         — delegate to LLM scorer (only when API key is present)

All graders return (success: bool, score: float, failure_reason: str).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

GradeResult = Tuple[bool, float, str]   # (success, score 0-1, failure_reason)

# ─────────────────────────────────────────────────────────────────────────────

def grade_task(task: Dict[str, Any], output: str) -> GradeResult:
    """Dispatch to the correct grader based on task['grading']['method']."""
    grading = task.get("grading", {})
    method = grading.get("method", "keyword_contains")

    if not output or output.startswith("[stub-response]") or output.startswith("[llm-error-fallback]"):
        return False, 0.0, "stub_or_error_response_no_api_key"

    graders = {
        "keyword_contains": _grade_keyword_contains,
        "numeric_match":    _grade_numeric_match,
        "format_check":      _grade_format_check,
        "rubric_score":      _grade_rubric_score,
        "plan_quality":      _grade_plan_quality,
        "llm_judge":         _grade_llm_judge,
    }
    grader = graders.get(method, _grade_keyword_contains)
    try:
        return grader(grading, output, task)
    except Exception as e:  # noqa: BLE001
        return False, 0.0, f"grader_error:{e}"


# ── keyword_contains ──────────────────────────────────────────────────────────

def _grade_keyword_contains(
    grading: Dict, output: str, task: Dict
) -> GradeResult:
    """Check that the output contains at least min_matches of the required keywords."""
    keywords: List[str] = grading.get("keywords", [])
    min_matches: int = grading.get("min_matches", max(1, len(keywords) // 2))
    max_words_check: Optional[int] = grading.get("max_words_check")

    output_lower = output.lower()
    matched = [kw for kw in keywords if kw.lower() in output_lower]
    match_count = len(matched)

    # Optional word-count constraint
    word_penalty = 0.0
    if max_words_check:
        word_count = len(output.split())
        if word_count > max_words_check:
            word_penalty = 0.15  # penalise but don't auto-fail

    if match_count >= min_matches:
        score = min(1.0, match_count / len(keywords)) - word_penalty
        return True, max(0.0, score), ""
    else:
        return (
            False,
            match_count / len(keywords) if keywords else 0.0,
            f"keyword_mismatch: found {match_count}/{len(keywords)}, need {min_matches}",
        )


# ── numeric_match ─────────────────────────────────────────────────────────────

def _grade_numeric_match(
    grading: Dict, output: str, task: Dict
) -> GradeResult:
    """Check that the output contains a number within tolerance of expected_value."""
    expected: float = float(grading.get("expected_value", 0))
    tolerance: float = float(grading.get("tolerance", 0.01))

    numbers = re.findall(r"-?\d+(?:\.\d+)?", output.replace(",", ""))
    if not numbers:
        return False, 0.0, f"no_number_found: expected {expected}"

    for num_str in numbers:
        val = float(num_str)
        if abs(val - expected) <= tolerance:
            return True, 1.0, ""
        if abs(val - expected) <= tolerance * 10:
            return True, 0.7, f"approximate_match: got {val}, expected {expected}"

    # Score partial credit for closest number
    closest = min((abs(float(n.replace(",", "")) - expected), n) for n in numbers)
    dist_ratio = closest[0] / (abs(expected) + 1)
    partial = max(0.0, 1.0 - min(dist_ratio, 1.0))
    return False, partial * 0.5, f"numeric_mismatch: closest={closest[1]}, expected={expected}"


# ── format_check ──────────────────────────────────────────────────────────────

def _grade_format_check(
    grading: Dict, output: str, task: Dict
) -> GradeResult:
    """Verify output matches structural constraints."""
    checks: List[str] = grading.get("checks", [])
    score_sum = 0.0
    failures = []

    for check in checks:
        if check == "is_json":
            try:
                json.loads(output)
                score_sum += 1.0
            except json.JSONDecodeError:
                failures.append("not_valid_json")
        elif check == "has_bullet_points":
            if re.search(r"^[\-\*\•]\s", output, re.MULTILINE):
                score_sum += 1.0
            else:
                failures.append("no_bullet_points")
        elif check == "has_numbered_list":
            if re.search(r"^\d+[\.\)]\s", output, re.MULTILINE):
                score_sum += 1.0
            else:
                failures.append("no_numbered_list")
        elif check.startswith("min_words:"):
            n = int(check.split(":")[1])
            if len(output.split()) >= n:
                score_sum += 1.0
            else:
                failures.append(f"too_short:{len(output.split())}<{n}")
        elif check.startswith("max_words:"):
            n = int(check.split(":")[1])
            if len(output.split()) <= n:
                score_sum += 1.0
            else:
                failures.append(f"too_long:{len(output.split())}>{n}")

    if not checks:
        return True, 1.0, ""

    score = score_sum / len(checks)
    success = score >= grading.get("min_score", 0.6)
    return success, score, "; ".join(failures) if failures else ""


# ── rubric_score ──────────────────────────────────────────────────────────────

def _grade_rubric_score(
    grading: Dict, output: str, task: Dict
) -> GradeResult:
    """Score based on a weighted rubric of keyword and structural checks."""
    criteria: List[Dict] = grading.get("criteria", [])
    if not criteria:
        return True, 0.5, "empty_rubric"

    total_weight = sum(c.get("weight", 1.0) for c in criteria)
    earned = 0.0
    failures = []
    output_lower = output.lower()

    for crit in criteria:
        weight = crit.get("weight", 1.0)
        required = crit.get("keywords", [])
        if required and any(kw.lower() in output_lower for kw in required):
            earned += weight
        else:
            failures.append(crit.get("name", "unnamed_criterion"))

    score = earned / total_weight if total_weight > 0 else 0.0
    min_score = grading.get("min_score", 0.5)
    success = score >= min_score
    reason = "; ".join(f"missing:{f}" for f in failures) if not success else ""
    return success, score, reason


# ── plan_quality ──────────────────────────────────────────────────────────────

def _grade_plan_quality(
    grading: Dict, output: str, task: Dict
) -> GradeResult:
    """Check that the output has a step-like structure (for planner evals)."""
    output_lower = output.lower()
    step_patterns = [
        r"step\s*\d",
        r"^\d+[\.\)]\s",
        r"first[,\s]", r"second[,\s]", r"then[,\s]", r"finally[,\s]",
    ]
    has_steps = any(
        re.search(p, output_lower, re.MULTILINE) for p in step_patterns
    )
    keywords: List[str] = grading.get("keywords", [])
    matched = sum(1 for kw in keywords if kw.lower() in output_lower) if keywords else 0

    structure_score = 0.5 if has_steps else 0.0
    keyword_score = (matched / len(keywords)) * 0.5 if keywords else 0.5

    score = structure_score + keyword_score
    success = score >= grading.get("min_score", 0.4)
    failure = "" if success else f"plan_quality_low: score={score:.2f}"
    return success, score, failure


# ── llm_judge ─────────────────────────────────────────────────────────────────

def _grade_llm_judge(
    grading: Dict, output: str, task: Dict
) -> GradeResult:
    """Use the LLM itself as a grader (fallback when no deterministic rubric applies).

    Skipped (returns 0.0) if no LLM API key is present, to keep evals runnable
    without cloud dependencies.
    """
    api_key = (
        os.getenv("AZURE_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GITHUB_MODELS_API_KEY")
    )
    if not api_key:
        return False, 0.0, "llm_judge_skipped_no_api_key"

    rubric_text = grading.get("rubric", "Is the response accurate and complete?")
    task_input = task.get("input", "")

    judge_prompt = (
        "You are an objective evaluator. Score the following response 0.0–1.0.\n"
        f"Task: {task_input[:500]}\n"
        f"Rubric: {rubric_text}\n"
        f"Response to evaluate:\n{output[:800]}\n\n"
        "Reply with ONLY a JSON object: {\"score\": <float 0-1>, \"reason\": \"<brief reason>\"}"
    )

    try:
        from src.agent.agents import _call_llm  # avoid circular at module level
        raw = _call_llm(judge_prompt, "llm.judge")
        # Parse score
        m = re.search(r'"score"\s*:\s*([0-9.]+)', raw)
        reason_m = re.search(r'"reason"\s*:\s*"([^"]+)"', raw)
        if m:
            score = float(m.group(1))
            reason = reason_m.group(1) if reason_m else ""
            return score >= 0.6, min(1.0, score), reason
    except Exception as e:  # noqa: BLE001
        return False, 0.0, f"llm_judge_error:{e}"

    return False, 0.0, "llm_judge_parse_failed"


__all__ = ["grade_task", "GradeResult"]
