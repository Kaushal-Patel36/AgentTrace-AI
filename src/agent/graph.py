"""LangGraph workflow assembly for the multi-agent pipeline.

Graph: planner → worker → reflector → reviewer

GraphState has been extended to carry:
  - run_id        : unique ID for checkpointing and eval correlation
  - tool_calls    : list of tool invocations made by the worker
  - tool_results  : structured output from each tool call
  - retry_count   : cumulative retries across all agent LLM calls
  - fallback_used : whether any node used a fallback strategy
  - failure_reason: populated when a node detects a quality failure
  - eval_context  : eval metadata (eval_run_id, task_id, prompt_version, …)
"""

from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph

from .agents import (
    planner_agent,
    reflection_agent,
    reviewer_agent,
    worker_agent,
)


class GraphState(TypedDict, total=False):
    # ── Core task fields (existing) ────────────────────────────────────────
    task: str
    plan: str
    work: str
    reflection: str
    review: str

    # ── Run lifecycle ──────────────────────────────────────────────────────
    run_id: str

    # ── Tool calling ───────────────────────────────────────────────────────
    tool_calls: List[Dict[str, Any]]
    tool_results: List[Dict[str, Any]]

    # ── Resilience tracking ────────────────────────────────────────────────
    retry_count: int
    fallback_used: bool
    failure_reason: Optional[str]

    # ── Eval / experiment metadata ─────────────────────────────────────────
    eval_context: Dict[str, Any]


def build_graph() -> Any:
    """Build and compile the multi-agent LangGraph workflow."""
    graph = StateGraph(GraphState)

    graph.add_node("planner", planner_agent)
    graph.add_node("worker", worker_agent)
    # Node name must differ from state key "reflection" to avoid collision
    graph.add_node("reflector", reflection_agent)
    graph.add_node("reviewer", reviewer_agent)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "worker")
    graph.add_edge("worker", "reflector")
    graph.add_edge("reflector", "reviewer")
    graph.set_finish_point("reviewer")

    return graph.compile()


__all__ = ["build_graph", "GraphState"]
