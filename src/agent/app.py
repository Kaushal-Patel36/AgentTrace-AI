"""FastAPI entrypoint exposing the multi-agent LangGraph workflow.

Endpoints:
  POST /run                  — Execute the full agent workflow
  GET  /health               — Health check
  GET  /eval/sample          — Run a sample task and return structured eval result
  GET  /experiments/latest   — Return the most recent experiment comparison table

Run locally:
  uvicorn src.agent.app:app --reload
"""

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .graph import build_graph
from .instrumentation import get_tracer
from .persistence import get_store

# ── Initialise telemetry FIRST ─────────────────────────────────────────────
_tracer = get_tracer()

app = FastAPI(
    title="LangGraph Multi-Agent Demo",
    version="0.2.0",
    description=(
        "Production-style multi-agent workflow with tool calling, "
        "checkpointing, retries, and OpenTelemetry observability."
    ),
)

# Auto-instrument FastAPI request spans
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore

    FastAPIInstrumentor.instrument_app(app)
except Exception as e:  # pragma: no cover
    print(f"Warning: FastAPI instrumentation failed: {e}")

_graph = build_graph()

# ── Request / response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    task: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/run")
def run_graph(req: RunRequest):
    """Execute the multi-agent workflow with full OpenTelemetry tracing.

    Creates a parent span 'workflow.execution' encompassing all agent spans
    and LLM calls, providing end-to-end observability.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("workflow.execution") as span:
        span.set_attribute("workflow.task", req.task)
        span.set_attribute("workflow.task_length", len(req.task))

        state = {"task": req.task}
        result = _graph.invoke(state)

        span.set_attribute("workflow.completed", True)
        span.set_attribute("workflow.retry_count", result.get("retry_count", 0))
        span.set_attribute("workflow.tool_calls", len(result.get("tool_calls") or []))
        if result.get("plan"):
            span.set_attribute("workflow.plan_length", len(result["plan"]))
        if result.get("work"):
            span.set_attribute("workflow.work_length", len(result["work"]))
        if result.get("review"):
            span.set_attribute("workflow.review_length", len(result["review"]))

        return {
            "run_id": result.get("run_id"),
            "task": req.task,
            "plan": result.get("plan"),
            "work": result.get("work"),
            "review": result.get("review"),
            "tool_calls": result.get("tool_calls") or [],
            "retry_count": result.get("retry_count", 0),
            "fallback_used": result.get("fallback_used", False),
        }


@app.get("/health")
def health():
    """Liveness / readiness probe."""
    return {"status": "ok", "version": "0.2.0"}


@app.get("/eval/sample")
def eval_sample():
    """Run a single sample eval task and return structured results.

    Useful for smoke-testing the evaluation pipeline without needing the CLI.
    """
    sample_task = (
        "What is 15% of 450? Also, briefly explain what OpenTelemetry is "
        "and why it matters for LLM applications."
    )
    tracer = get_tracer()
    with tracer.start_as_current_span("eval.sample_run") as span:
        span.set_attribute("eval.sample", True)
        state = {"task": sample_task}
        result = _graph.invoke(state)

    return {
        "sample_task": sample_task,
        "run_id": result.get("run_id"),
        "plan": result.get("plan"),
        "work": result.get("work"),
        "review": result.get("review"),
        "tool_calls": result.get("tool_calls") or [],
        "retry_count": result.get("retry_count", 0),
        "note": (
            "Set OPENAI_API_KEY or AZURE_OPENAI_* env vars for real LLM responses. "
            "Stub responses are returned when no key is present."
        ),
    }


@app.get("/experiments/latest")
def experiments_latest():
    """Return the most recent experiment comparison table from results/.

    Falls back to a sample result if no experiment has been run yet.
    """
    results_dir = Path("results")
    md_files = sorted(results_dir.glob("*.md"), reverse=True) if results_dir.exists() else []
    csv_files = sorted(results_dir.glob("*.csv"), reverse=True) if results_dir.exists() else []

    # Try to load and return the latest comparison
    if md_files:
        try:
            content = md_files[0].read_text()
            return JSONResponse(
                content={
                    "source": str(md_files[0]),
                    "format": "markdown",
                    "content": content,
                    "csv_available": str(csv_files[0]) if csv_files else None,
                }
            )
        except Exception:
            pass

    # Inline fallback
    return {
        "message": "No experiment results found. Run `make experiment` to generate them.",
        "hint": "Results will appear at results/comparison_<timestamp>.md",
        "sample_table": (
            "| Variant | Success Rate | Avg Score | P50 Latency | P95 Latency | Avg Cost |\n"
            "|---------|-------------|-----------|-------------|-------------|----------|\n"
            "| prompt_v1 + retry_off | 72% | 0.68 | 1100ms | 3200ms | $0.0003 |\n"
            "| prompt_v2 + retry_on  | 85% | 0.81 | 1250ms | 3600ms | $0.0005 |"
        ),
    }


@app.get("/runs")
def list_runs(limit: int = 20):
    """List recent agent runs stored in the checkpoint database."""
    try:
        runs = get_store().list_runs(limit=limit)
        return {"runs": runs, "count": len(runs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    """Retrieve all checkpoints for a specific run (for replay / debug)."""
    try:
        store = get_store()
        steps = store.list_steps(run_id)
        if not steps:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        checkpoints = []
        for step in steps:
            cp = store.load(run_id, step)
            if cp:
                checkpoints.append({
                    "step": cp.step,
                    "timestamp": cp.timestamp,
                    "state_keys": list(cp.state.keys()),
                    "metadata": cp.metadata,
                })
        return {"run_id": run_id, "steps": steps, "checkpoints": checkpoints}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
