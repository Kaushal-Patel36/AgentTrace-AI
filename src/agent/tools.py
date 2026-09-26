"""Tool registry and built-in tools for the multi-agent workflow.

Tools expose a standard interface:
    tool_fn(input: dict) -> dict

Each tool is automatically wrapped with OpenTelemetry tracing via ToolRegistry.call().
Two built-in tools are provided:
  - calculator: safe AST-based math expression evaluator
  - retrieval:  mock document retrieval from an in-memory knowledge base
"""

from __future__ import annotations

import ast
import math
import operator
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from opentelemetry import trace as otel_trace

from .instrumentation import get_tracer


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """Structured result from a single tool call."""

    tool_name: str
    success: bool
    output: Any
    error: Optional[str] = None
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "metadata": self.metadata,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

class ToolRegistry:
    """Registry of available tools with telemetry-wrapped execution.

    Usage::

        registry = ToolRegistry()
        registry.register("calc", calculator_tool, schema={...})
        result = registry.call("calc", {"expression": "2 + 2"})
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable[[dict], dict]] = {}
        self._schemas: Dict[str, dict] = {}

    def register(self, name: str, fn: Callable[[dict], dict], schema: dict) -> None:
        self._tools[name] = fn
        self._schemas[name] = schema

    def list_tools(self) -> List[dict]:
        """Return list of tool schemas for prompt injection."""
        return [{"name": k, "schema": v} for k, v in self._schemas.items()]

    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def call(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute a tool by name, wrapping the call in an OTEL span."""
        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                output=None,
                error=f"Unknown tool '{tool_name}'. Available: {self.tool_names()}",
            )

        tracer = get_tracer()
        with tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("tool.input", str(tool_input)[:500])

            start = time.time()
            try:
                raw = self._tools[tool_name](tool_input)
                latency_ms = (time.time() - start) * 1000

                span.set_attribute("tool.success", True)
                span.set_attribute("tool.latency_ms", round(latency_ms, 2))
                span.set_attribute("tool.output_preview", str(raw)[:200])
                span.set_status(otel_trace.Status(otel_trace.StatusCode.OK))

                return ToolResult(
                    tool_name=tool_name,
                    success=True,
                    output=raw,
                    latency_ms=latency_ms,
                )
            except Exception as e:  # noqa: BLE001
                latency_ms = (time.time() - start) * 1000
                span.record_exception(e)
                span.set_attribute("tool.success", False)
                span.set_attribute("tool.error", str(e)[:300])
                span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, str(e)))

                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    output=None,
                    error=str(e),
                    latency_ms=latency_ms,
                )


# ── Calculator tool ───────────────────────────────────────────────────────────

_SAFE_OPS: Dict[type, Callable] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

_SAFE_FUNCS: Dict[str, Any] = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "log2": math.log2,
    "log10": math.log10, "exp": math.exp, "floor": math.floor,
    "ceil": math.ceil, "pi": math.pi, "e": math.e,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pow": pow,
}


def _safe_eval(node: ast.AST) -> Any:
    """Recursively evaluate a safe arithmetic AST node."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _SAFE_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS:
            fn = _SAFE_FUNCS[node.func.id]
            if callable(fn):
                args = [_safe_eval(a) for a in node.args]
                return fn(*args)
            raise ValueError(f"'{node.func.id}' is a constant, not a function")
        raise ValueError(f"Unsafe function call: {getattr(node.func, 'id', '?')}")
    if isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCS:
            return _SAFE_FUNCS[node.id]
        raise ValueError(f"Unknown name: {node.id}")
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def calculator_tool(tool_input: dict) -> dict:
    """Evaluate a mathematical expression safely.

    Input:  {"expression": "sqrt(144) + 15 * 0.15"}
    Output: {"result": 14.25, "expression": "sqrt(144) + 15 * 0.15"}
    """
    expression = str(tool_input.get("expression", "")).strip()
    if not expression:
        raise ValueError("No expression provided. Pass {'expression': '<math expr>'}")
    if len(expression) > 500:
        raise ValueError("Expression too long (max 500 characters)")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}") from e
    result = _safe_eval(tree.body)
    return {"result": result, "expression": expression}


# ── Retrieval tool ────────────────────────────────────────────────────────────

_KNOWLEDGE_BASE: Dict[str, str] = {
    "opentelemetry": (
        "OpenTelemetry is a CNCF observability framework providing vendor-neutral APIs, SDKs, "
        "and tooling for generating, collecting, and exporting telemetry data (traces, metrics, logs)."
    ),
    "langgraph": (
        "LangGraph is a library for building stateful, multi-actor applications with LLMs, built on "
        "LangChain. It models agent workflows as directed graphs where nodes are agents and edges "
        "are transitions between steps."
    ),
    "fastapi": (
        "FastAPI is a modern, high-performance Python web framework built on Starlette and Pydantic. "
        "It uses Python type hints for validation and auto-generates OpenAPI documentation."
    ),
    "azure monitor": (
        "Azure Monitor is Microsoft's full-stack observability platform. Application Insights, a "
        "sub-service, ingests OTEL traces, metrics, and logs and offers KQL-based analysis."
    ),
    "agent": (
        "An AI agent is an autonomous system that perceives its environment, reasons using an LLM, "
        "and takes actions (tool calls, API requests) to accomplish goals across multiple steps."
    ),
    "token": (
        "Tokens are the smallest text units LLMs process. Costs and context limits are measured in "
        "tokens. Roughly 1 token ≈ 4 English characters. GPT-4o-mini costs ~$0.15/M input tokens."
    ),
    "telemetry": (
        "Telemetry refers to the automatic collection of metrics, logs, and traces from a running "
        "system. The three pillars — metrics (values over time), logs (events), traces (request flows) "
        "— together enable full observability."
    ),
    "rag": (
        "Retrieval-Augmented Generation (RAG) combines semantic document retrieval with LLM generation "
        "to produce grounded, fact-based answers from a knowledge base, reducing hallucination."
    ),
    "vector database": (
        "Vector databases (e.g., Pinecone, Weaviate, pgvector) store high-dimensional embeddings and "
        "support approximate nearest-neighbor (ANN) search for semantic similarity at scale."
    ),
    "llm": (
        "Large Language Models (LLMs) are neural networks (typically transformers) trained on massive "
        "text corpora. They can generate, summarize, translate, classify, and reason over text."
    ),
    "observability": (
        "Observability is the degree to which a system's internal state can be inferred from its "
        "external outputs. High observability = fast MTTR and easier capacity planning."
    ),
    "terraform": (
        "Terraform by HashiCorp is an infrastructure-as-code (IaC) tool using HCL to provision and "
        "manage cloud resources declaratively with plan/apply workflow and remote state."
    ),
    "checkpointing": (
        "Checkpointing in LLM agent workflows means persisting intermediate agent state (plan, tool "
        "results, partial outputs) to a durable store, enabling resume on failure and replay."
    ),
    "retry": (
        "Retry logic with exponential backoff is a resilience pattern: failed operations are retried "
        "with increasing delays (base * factor^attempt + jitter) up to a maximum count."
    ),
    "evaluation": (
        "LLM evaluation (evals) systematically measures model output quality across a benchmark "
        "dataset. Metrics include accuracy, task success rate, latency, cost, and robustness."
    ),
}


def retrieval_tool(tool_input: dict) -> dict:
    """Retrieve relevant document snippets from the knowledge base.

    Input:  {"query": "how does opentelemetry work"}
    Output: {"query": "...", "results": [{"source": "...", "content": "...", "score": 0.92}], ...}
    """
    query = str(tool_input.get("query", "")).lower().strip()
    if not query:
        raise ValueError("No query provided. Pass {'query': '<search query>'}")

    query_words = set(query.split())
    scored: List[tuple[float, str, str]] = []

    for key, content in _KNOWLEDGE_BASE.items():
        key_words = set(key.split())
        # Exact key match scores highest
        if key in query:
            score = 0.95
        else:
            overlap = len(query_words & key_words)
            content_hits = sum(1 for w in query_words if w in content.lower())
            score = 0.0
            if overlap:
                score = 0.60 + 0.10 * overlap
            elif content_hits:
                score = 0.30 + 0.05 * content_hits

        if score > 0.0:
            scored.append((score, key, content))

    scored.sort(reverse=True)
    results = [
        {"source": key, "content": content, "score": round(score, 2)}
        for score, key, content in scored[:3]
    ]

    if not results:
        results = [{"source": "general", "content": "No relevant documents found for this query.", "score": 0.0}]

    return {
        "query": query,
        "results": results,
        "total_found": len(scored),
    }


# ── Default global registry ───────────────────────────────────────────────────

_registry = ToolRegistry()
_registry.register(
    "calculator",
    calculator_tool,
    schema={
        "name": "calculator",
        "description": "Safely evaluate a mathematical expression and return the numeric result.",
        "parameters": {
            "expression": {
                "type": "string",
                "description": "A valid Python-compatible math expression, e.g. 'sqrt(144) + 15 * 0.15'",
            }
        },
    },
)
_registry.register(
    "retrieval",
    retrieval_tool,
    schema={
        "name": "retrieval",
        "description": "Retrieve relevant document snippets from the knowledge base for a query.",
        "parameters": {
            "query": {
                "type": "string",
                "description": "A natural-language search query, e.g. 'how does LangGraph work'",
            }
        },
    },
)


def get_registry() -> ToolRegistry:
    """Return the shared global tool registry."""
    return _registry


__all__ = ["ToolRegistry", "ToolResult", "get_registry", "calculator_tool", "retrieval_tool"]
