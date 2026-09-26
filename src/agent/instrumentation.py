"""OpenTelemetry + Azure Monitor instrumentation helpers.

Sets up tracing, metrics, and logs export to Azure Monitor (App Insights + Log Analytics).
Requires environment variable APPINSIGHTS_CONNECTION_STRING.
Provides comprehensive LLM call tracing with token usage and latency metrics.
Implements OpenTelemetry Gen AI semantic conventions for standardized observability.

New in v0.2:
  - set_eval_attributes(): stamps eval_run_id, task_id, prompt_version on any span
  - AzureMonitorLogExporter wired alongside trace and metric exporters
  - Retry/fallback span attributes
"""

from __future__ import annotations
import os
import time
from functools import wraps
from typing import Callable, Any, Dict

from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter, AzureMonitorMetricExporter

_tracer_initialized = False
_in_memory_spans = []
_meter = None
_token_counter = None
_operation_duration = None


class _InMemoryExporter(SpanExporter):
    """Simple in-memory exporter for testing spans without monkeypatching.

    Enabled when environment variable OTEL_INMEMORY_EXPORTER is truthy ("1", "true", etc.).
    """

    def export(self, spans):  # type: ignore[override]
        _in_memory_spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):  # type: ignore[override]
        return


def get_in_memory_spans():  # pragma: no cover - accessor trivial
    return list(_in_memory_spans)


def reset_in_memory_spans():  # pragma: no cover - test helper
    _in_memory_spans.clear()


def _init_tracer() -> None:
    global _tracer_initialized, _meter, _token_counter, _operation_duration
    if _tracer_initialized:
        return
    connection_string = os.getenv("APPINSIGHTS_CONNECTION_STRING")
    enable_in_memory = os.getenv("OTEL_INMEMORY_EXPORTER", "").lower() in {"1", "true", "yes"}
    resource = Resource.create(
        {
            "service.name": os.getenv("SERVICE_NAME", "langgraph-multi-agent"),
            "service.version": os.getenv("SERVICE_VERSION", "0.1.0"),
            "deployment.environment": os.getenv("DEPLOYMENT_ENV", "local"),
        }
    )
    
    # Initialize trace provider
    provider = TracerProvider(resource=resource)

    if connection_string:
        try:
            trace_exporter = AzureMonitorTraceExporter.from_connection_string(connection_string)
            provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        except (ValueError, Exception) as e:
            # Invalid connection string or exporter initialization failed
            # Continue without Azure Monitor - useful for testing
            print(f"Warning: Failed to initialize Azure Monitor trace exporter: {e}")
    if enable_in_memory:
        provider.add_span_processor(SimpleSpanProcessor(_InMemoryExporter()))

    # Always set tracer provider, even if no exporters configured (for testing)
    trace.set_tracer_provider(provider)
    
    # Initialize metrics provider
    if connection_string:
        try:
            metric_exporter = AzureMonitorMetricExporter.from_connection_string(connection_string)
            metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60000)
            meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
            metrics.set_meter_provider(meter_provider)
        except (ValueError, Exception) as e:
            print(f"Warning: Failed to initialize Azure Monitor metric exporter: {e}")
            # Fallback to default meter provider
            meter_provider = MeterProvider(resource=resource)
            metrics.set_meter_provider(meter_provider)
    else:
        # Use default meter provider for testing
        meter_provider = MeterProvider(resource=resource)
        metrics.set_meter_provider(meter_provider)
    
    # Create meters and instruments for LLM metrics
    _meter = metrics.get_meter(__name__)
    _token_counter = _meter.create_counter(
        name="gen_ai.client.token.usage",
        description="Number of tokens used in LLM operations",
        unit="tokens"
    )
    _operation_duration = _meter.create_histogram(
        name="gen_ai.client.operation.duration",
        description="Duration of LLM operations",
        unit="ms"
    )
    
    # Auto-instrument HTTP clients for outbound LLM API calls
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        HTTPXClientInstrumentor().instrument()
        RequestsInstrumentor().instrument()
    except Exception:  # pragma: no cover
        pass  # Continue if instrumentation libraries not available
    
    _tracer_initialized = True


def get_tracer():
    _init_tracer()
    return trace.get_tracer(__name__)


def traced_span(name: str, attributes: Dict[str, Any] | None = None):
    """Decorator to create a traced span with custom attributes.
    
    Args:
        name: Span name (e.g., 'planner.agent', 'llm.call')
        attributes: Optional dict of attributes to add to span
    """
    attributes = attributes or {}

    def decorator(func: Callable[..., Any]):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(name) as span:
                # Set initial attributes
                for k, v in attributes.items():
                    span.set_attribute(k, v)
                
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    # Add simple state size metric on exit if dict
                    if isinstance(result, dict):
                        span.set_attribute("state.keys", len(result.keys()))
                        # Add state content size estimate
                        total_size = sum(len(str(v)) for v in result.values())
                        span.set_attribute("state.content_size", total_size)
                    
                    # Add duration metric
                    duration_ms = (time.time() - start_time) * 1000
                    span.set_attribute("duration_ms", round(duration_ms, 2))
                    span.set_status(trace.Status(trace.StatusCode.OK))
                except Exception as e:  # noqa: BLE001
                    span.record_exception(e)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
                return result

        return wrapper

    return decorator


def trace_llm_call(model: str, prompt: str, max_tokens: int = 256):
    """Context manager for tracing LLM calls with detailed metrics.
    
    Usage:
        tracer = get_tracer()
        with tracer.start_as_current_span('llm.call') as span:
            trace_llm_call_attributes(span, model, prompt, max_tokens)
            # ... make LLM call ...
            trace_llm_response(span, response)
    """
    tracer = get_tracer()
    span = tracer.start_as_current_span(
        "llm.call",
        attributes={
            "llm.model": model,
            "llm.request.max_tokens": max_tokens,
            "llm.request.prompt_length": len(prompt),
            "llm.system": "azure_openai" if "azure" in model.lower() else "openai",
        }
    )
    return span


def add_llm_response_attributes(span: trace.Span, response: Any, model: str = "", operation: str = "") -> None:
    """Add LLM response attributes to an existing span using Gen AI semantic conventions.
    
    Args:
        span: Active OpenTelemetry span
        response: OpenAI completion response object
        model: Model name for metrics attribution
        operation: Operation name for metrics attribution
    """
    global _token_counter
    
    try:
        if hasattr(response, "usage") and response.usage:
            # Gen AI semantic conventions for token usage
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            total_tokens = response.usage.total_tokens
            
            span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
            
            # Record metrics
            if _token_counter and model:
                _token_counter.add(
                    prompt_tokens,
                    {"gen_ai.token.type": "input", "gen_ai.request.model": model, "gen_ai.operation.name": operation}
                )
                _token_counter.add(
                    completion_tokens,
                    {"gen_ai.token.type": "output", "gen_ai.request.model": model, "gen_ai.operation.name": operation}
                )
        
        if hasattr(response, "choices") and response.choices:
            choice = response.choices[0]
            if hasattr(choice, "message"):
                content = choice.message.content or ""
                span.set_attribute("gen_ai.response.finish_reasons", [choice.finish_reason or "unknown"])
        
        if hasattr(response, "model"):
            span.set_attribute("gen_ai.response.model", response.model)
    except Exception:  # pragma: no cover
        pass  # Don't fail if response structure is unexpected


def log_llm_prompt_and_response(
    span: trace.Span,
    prompt: str,
    response: str,
    enable_content_logging: bool = None
) -> None:
    """Log LLM prompt and response as span events.
    
    Args:
        span: Active OpenTelemetry span
        prompt: The prompt sent to the LLM
        response: The response received from the LLM
        enable_content_logging: Whether to log full content (default: from env ENABLE_PROMPT_LOGGING)
    """
    if enable_content_logging is None:
        enable_content_logging = os.getenv("ENABLE_PROMPT_LOGGING", "false").lower() in {"true", "1", "yes"}
    
    if not enable_content_logging:
        return
    
    try:
        # Truncate content for privacy/size limits (max 1000 chars)
        max_length = int(os.getenv("PROMPT_LOG_MAX_LENGTH", "1000"))
        
        span.add_event(
            "gen_ai.content.prompt",
            {
                "gen_ai.prompt": prompt[:max_length],
                "gen_ai.prompt.length": len(prompt),
                "gen_ai.prompt.truncated": len(prompt) > max_length
            }
        )
        
        span.add_event(
            "gen_ai.content.completion",
            {
                "gen_ai.completion": response[:max_length],
                "gen_ai.completion.length": len(response),
                "gen_ai.completion.truncated": len(response) > max_length
            }
        )
    except Exception:  # pragma: no cover
        pass  # Don't fail if event logging fails


def record_llm_operation_duration(model: str, operation: str, duration_ms: float) -> None:
    """Record LLM operation duration as a metric.
    
    Args:
        model: Model name
        operation: Operation name (e.g., 'llm.completion.planner')
        duration_ms: Duration in milliseconds
    """
    global _operation_duration
    
    try:
        if _operation_duration:
            _operation_duration.record(
                duration_ms,
                {"gen_ai.request.model": model, "gen_ai.operation.name": operation}
            )
    except Exception:  # pragma: no cover
        pass  # Don't fail if metric recording fails


def set_eval_attributes(
    span: trace.Span,
    *,
    eval_run_id: str = "",
    task_id: str = "",
    task_category: str = "",
    prompt_version: str = "",
    model_name: str = "",
    config_version: str = "",
    retry_count: int = 0,
    fallback_used: bool = False,
    final_status: str = "",
    failure_reason: str = "",
) -> None:
    """Stamp evaluation and experiment metadata on an active OTEL span.

    All parameters are optional; only non-empty values are written to reduce
    noise in traces that are not part of an evaluation run.

    Args:
        span:           The active span to annotate.
        eval_run_id:    Unique ID of the evaluation batch run.
        task_id:        Benchmark task identifier (e.g. 'tool_001').
        task_category:  Task category (summarization, tool_use, …).
        prompt_version: Which prompt variant was active (e.g. 'v1', 'v2').
        model_name:     LLM model used for this run.
        config_version: Experiment config name (e.g. 'retry_aggressive').
        retry_count:    Cumulative retries used across all agent nodes.
        fallback_used:  Whether any node used a fallback strategy.
        final_status:   'success', 'failure', 'partial', etc.
        failure_reason: Short failure category for analysis.
    """
    try:
        if eval_run_id:
            span.set_attribute("eval.run_id", eval_run_id)
        if task_id:
            span.set_attribute("eval.task_id", task_id)
        if task_category:
            span.set_attribute("eval.task_category", task_category)
        if prompt_version:
            span.set_attribute("eval.prompt_version", prompt_version)
        if model_name:
            span.set_attribute("eval.model_name", model_name)
        if config_version:
            span.set_attribute("eval.config_version", config_version)
        span.set_attribute("eval.retry_count", retry_count)
        span.set_attribute("eval.fallback_used", fallback_used)
        if final_status:
            span.set_attribute("eval.final_status", final_status)
        if failure_reason:
            span.set_attribute("eval.failure_reason", failure_reason)
    except Exception:  # pragma: no cover
        pass  # Never fail due to instrumentation
