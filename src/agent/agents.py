"""Agent implementations for the LangGraph multi-agent workflow.

Agents:
  planner   → breaks the task into a 2-step plan
  worker    → executes the plan, calling tools when needed
  reflector → critiques the work output
  reviewer  → produces the final summary

Each agent:
  - Is wrapped with @traced_span for OTEL tracing
  - Applies a configurable RetryPolicy to its LLM call
  - Saves a checkpoint after completion
  - Emits telemetry for retry count and tool usage
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

from .instrumentation import (
    add_llm_response_attributes,
    get_tracer,
    log_llm_prompt_and_response,
    record_llm_operation_duration,
    traced_span,
)
from .persistence import Checkpoint, get_store, make_run_id
from .retry import RetryPolicy, call_with_retry
from .tools import ToolResult, get_registry

# ── Module-level configurable state ──────────────────────────────────────────
# These can be overridden by callers (e.g. experiment runner) before invoking
# the graph.  Thread-local isolation is not implemented; this is single-threaded
# eval-friendly design.

_retry_policy: RetryPolicy = RetryPolicy.default()
_checkpoint_enabled: bool = True
_eval_context: Dict[str, Any] = {}   # injected by eval runner


def set_retry_policy(policy: RetryPolicy) -> None:
    global _retry_policy
    _retry_policy = policy


def set_eval_context(ctx: Dict[str, Any]) -> None:
    global _eval_context
    _eval_context = ctx or {}


def get_eval_context() -> Dict[str, Any]:
    return _eval_context


def set_checkpoint_enabled(enabled: bool) -> None:
    global _checkpoint_enabled
    _checkpoint_enabled = enabled


# ── LLM call helper ───────────────────────────────────────────────────────────

def _call_llm(prompt: str, operation: str = "llm.completion") -> str:
    """Make an LLM call with OpenTelemetry tracing (no retry — use _call_llm_with_retry).

    Returns:
        The LLM response text (or a stub if no API key is configured).
    """
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

    api_key = azure_key or os.getenv("OPENAI_API_KEY") or os.getenv("GITHUB_MODELS_API_KEY")
    if not api_key:
        return f"[stub-response] {prompt[:80]}..."

    tracer = get_tracer()
    try:
        if azure_endpoint and azure_deployment:
            from openai import AzureOpenAI  # type: ignore

            with tracer.start_as_current_span(operation) as span:
                start_time = time.time()
                _set_gen_ai_request_attrs(span, "azure_openai", azure_deployment, operation)
                _set_eval_attrs_on_span(span)

                client = AzureOpenAI(
                    api_key=azure_key,
                    azure_endpoint=azure_endpoint,
                    api_version=azure_api_version,
                )
                completion = client.chat.completions.create(
                    model=azure_deployment,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=512,
                )
                duration_ms = (time.time() - start_time) * 1000

                add_llm_response_attributes(span, completion, model=azure_deployment, operation=operation)
                result = completion.choices[0].message.content or ""
                log_llm_prompt_and_response(span, prompt, result)
                record_llm_operation_duration(azure_deployment, operation, duration_ms)
                return result

        else:
            from openai import OpenAI  # type: ignore

            base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("GITHUB_MODELS_BASE_URL")
            model_name = os.getenv("MODEL_NAME", "openai/gpt-4o-mini")

            with tracer.start_as_current_span(operation) as span:
                start_time = time.time()
                _set_gen_ai_request_attrs(span, "openai", model_name, operation)
                _set_eval_attrs_on_span(span)

                client = (
                    OpenAI(api_key=api_key, base_url=base_url)
                    if base_url
                    else OpenAI(api_key=api_key)
                )
                completion = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=512,
                )
                duration_ms = (time.time() - start_time) * 1000

                add_llm_response_attributes(span, completion, model=model_name, operation=operation)
                result = completion.choices[0].message.content or ""
                log_llm_prompt_and_response(span, prompt, result)
                record_llm_operation_duration(model_name, operation, duration_ms)
                return result

    except Exception as e:  # noqa: BLE001
        return f"[llm-error-fallback] {e}"


def _call_llm_with_retry(prompt: str, operation: str) -> tuple[str, dict]:
    """Wrap _call_llm with the current global RetryPolicy."""
    tracer = get_tracer()
    with tracer.start_as_current_span(f"{operation}.with_retry") as span:
        _set_eval_attrs_on_span(span)
        result, meta = call_with_retry(
            fn=lambda: _call_llm(prompt, operation),
            policy=_retry_policy,
            operation_name=operation,
            span=span,
        )
        span.set_attribute("llm.retry_count", meta["retry_count"])
        span.set_attribute("llm.fallback_used", meta["fallback_used"])
        return result, meta


def _set_gen_ai_request_attrs(span: Any, system: str, model: str, operation: str) -> None:
    span.set_attribute("gen_ai.system", system)
    span.set_attribute("gen_ai.request.model", model)
    span.set_attribute("gen_ai.request.max_tokens", 512)
    span.set_attribute("gen_ai.operation.name", operation)


def _set_eval_attrs_on_span(span: Any) -> None:
    """Inject current eval context as span attributes."""
    ctx = _eval_context
    if ctx.get("eval_run_id"):
        span.set_attribute("eval.run_id", ctx["eval_run_id"])
    if ctx.get("task_id"):
        span.set_attribute("eval.task_id", ctx["task_id"])
    if ctx.get("prompt_version"):
        span.set_attribute("eval.prompt_version", ctx["prompt_version"])
    if ctx.get("config_version"):
        span.set_attribute("eval.config_version", ctx["config_version"])


def _save_checkpoint(run_id: str, step: str, state: Dict[str, Any]) -> None:
    if not _checkpoint_enabled or not run_id:
        return
    try:
        get_store().save(
            Checkpoint(
                run_id=run_id,
                step=step,
                state=state,
                timestamp=time.time(),
                metadata={
                    "retry_count": state.get("retry_count", 0),
                    "fallback_used": state.get("fallback_used", False),
                    **{f"eval_{k}": v for k, v in _eval_context.items()},
                },
            )
        )
    except Exception:  # pragma: no cover
        pass  # never block agent execution due to persistence failure


# ── Tool integration helpers ──────────────────────────────────────────────────

_TOOL_KEYWORDS: Dict[str, List[str]] = {
    "calculator": [
        "calculat", "comput", "math", "formula", "percent", "sum", "total",
        "average", "sqrt", "square root", "multiply", "divide", "ratio", "result",
    ],
    "retrieval": [
        "look up", "retrieve", "search", "find", "what is", "define", "explain",
        "document", "reference", "knowledge", "source", "information about",
    ],
}


def _detect_tool_needs(text: str) -> List[str]:
    """Detect which tools the plan text implies should be used."""
    text_lower = text.lower()
    needed = []
    for tool_name, keywords in _TOOL_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            needed.append(tool_name)
    return needed


def _extract_tool_input(tool_name: str, plan: str, task: str) -> dict:
    """Heuristically extract tool input from plan/task text."""
    if tool_name == "calculator":
        # Look for a math expression in the task
        expr_patterns = [
            r"(\d[\d\s\+\-\*\/\.\%\^\(\)]+\d)",
            r"(sqrt\([^)]+\))",
            r"(\d+\s*%\s*of\s*\d+)",
        ]
        for pat in expr_patterns:
            m = re.search(pat, task, re.IGNORECASE)
            if m:
                raw = m.group(1).strip()
                # Convert "15% of 450" to "0.15 * 450"
                pct_m = re.match(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", raw, re.I)
                if pct_m:
                    raw = f"{float(pct_m.group(1)) / 100} * {pct_m.group(2)}"
                return {"expression": raw}
        # Fall back to a generic expression from the task
        return {"expression": task.strip()[:200]}

    if tool_name == "retrieval":
        # Use key noun phrases from the task as the query
        return {"query": task.strip()[:300]}

    return {}


def _format_tool_results(tool_results: List[ToolResult]) -> str:
    """Format tool results as a context string for the LLM prompt."""
    if not tool_results:
        return ""
    lines = ["=== Tool Results ==="]
    for tr in tool_results:
        if tr.success:
            lines.append(f"[{tr.tool_name}] → {tr.output}")
        else:
            lines.append(f"[{tr.tool_name}] ERROR: {tr.error}")
    lines.append("=== End Tool Results ===")
    return "\n".join(lines)


# ── Agent nodes ───────────────────────────────────────────────────────────────

@traced_span("planner.agent")
def planner_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Break the user task into a concise 2-step plan."""
    task = state.get("task", "")
    run_id = state.get("run_id") or make_run_id()
    state["run_id"] = run_id

    plan, meta = _call_llm_with_retry(
        prompt=(
            f"You are a planning agent. Create a concise 2-step plan to accomplish this task.\n"
            f"Task: {task}\n"
            f"Respond with exactly: Step 1: ... Step 2: ..."
        ),
        operation="llm.completion.planner",
    )
    state["plan"] = plan
    state["retry_count"] = (state.get("retry_count") or 0) + meta["retry_count"]
    _save_checkpoint(run_id, "planner", state)
    return state


@traced_span("worker.agent")
def worker_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the plan, calling tools when the plan implies computation or retrieval."""
    plan = state.get("plan", "")
    task = state.get("task", "")
    run_id = state.get("run_id", "")

    # ── Tool calling ────────────────────────────────────────────────────────
    registry = get_registry()
    tool_results: List[ToolResult] = []
    needed_tools = _detect_tool_needs(plan + " " + task)

    for tool_name in needed_tools:
        tool_input = _extract_tool_input(tool_name, plan, task)
        result = registry.call(tool_name, tool_input)
        tool_results.append(result)

    state["tool_calls"] = [
        {"tool": tr.tool_name, "input": tr.metadata, "success": tr.success}
        for tr in tool_results
    ]
    state["tool_results"] = [tr.to_dict() for tr in tool_results]

    # ── LLM call with tool context ──────────────────────────────────────────
    tool_ctx = _format_tool_results(tool_results)
    prompt = (
        f"You are a worker agent. Execute the following plan and produce a concrete result.\n"
        f"Plan: {plan}\n"
    )
    if tool_ctx:
        prompt += f"\n{tool_ctx}\n"
    prompt += "Provide a thorough, detailed result based on the plan and any tool outputs above."

    work, meta = _call_llm_with_retry(prompt=prompt, operation="llm.completion.worker")
    state["work"] = work
    state["retry_count"] = (state.get("retry_count") or 0) + meta["retry_count"]
    state["fallback_used"] = meta["fallback_used"]
    _save_checkpoint(run_id, "worker", state)
    return state


@traced_span("reflection.agent")
def reflection_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Critique the work output and suggest improvements."""
    work = state.get("work", "")
    run_id = state.get("run_id", "")
    task = state.get("task", "")

    # Detect low-quality output before calling LLM
    if not work or len(work) < 20:
        state["reflection"] = "Work output was empty or too short. Unable to generate a meaningful reflection."
        state["failure_reason"] = "empty_work_output"
        _save_checkpoint(run_id, "reflector", state)
        return state

    reflection, meta = _call_llm_with_retry(
        prompt=(
            f"You are a senior AI reviewer. Validate the work output below for the given task.\n"
            f"Task: {task}\n"
            f"Work output (length={len(work)} chars):\n{work}\n\n"
            "Provide: (1) a terse validation summary, (2) one specific improvement suggestion."
        ),
        operation="llm.completion.reflection",
    )
    state["reflection"] = reflection
    state["retry_count"] = (state.get("retry_count") or 0) + meta["retry_count"]
    _save_checkpoint(run_id, "reflector", state)
    return state


@traced_span("reviewer.agent")
def reviewer_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Produce the final review and summary, incorporating reflection."""
    work = state.get("work", "")
    reflection = state.get("reflection", "")
    run_id = state.get("run_id", "")
    task = state.get("task", "")

    review, meta = _call_llm_with_retry(
        prompt=(
            f"You are a final reviewer. Produce a polished FINAL summary of the work below, "
            f"incorporating any improvements noted in the reflection.\n"
            f"Task: {task}\n"
            f"Work: {work}\n"
            f"Reflection: {reflection}\n\n"
            "Respond with the final, improved answer only."
        ),
        operation="llm.completion.reviewer",
    )
    state["review"] = review
    state["retry_count"] = (state.get("retry_count") or 0) + meta["retry_count"]

    _save_checkpoint(run_id, "reviewer", state)

    # Mark run complete in the store
    if run_id and _checkpoint_enabled:
        try:
            get_store().complete_run(run_id, state)
        except Exception:  # pragma: no cover
            pass

    return state


__all__ = [
    "planner_agent",
    "worker_agent",
    "reflection_agent",
    "reviewer_agent",
    "set_retry_policy",
    "set_eval_context",
    "set_checkpoint_enabled",
]
