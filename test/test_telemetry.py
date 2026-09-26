from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from src.agent.agents import planner_agent
from src.agent.instrumentation import traced_span


class InMemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans = []

    def export(self, spans):  # type: ignore[override]
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):  # type: ignore[override]
        return


def test_traced_span_creates_span(monkeypatch):
    exporter = InMemoryExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # Patch instrumentation.get_tracer to use our provider
    from src.agent import instrumentation

    def _fake_get_tracer():
        return provider.get_tracer(__name__)

    monkeypatch.setattr(instrumentation, "get_tracer", _fake_get_tracer)

    state = {"task": "Demo task"}
    result = planner_agent(state)
    assert "plan" in result
    assert len(exporter.spans) >= 1
    span = exporter.spans[-1]
    assert span.name == "planner.agent"
    # Attribute added by decorator when dict returned
    assert span.attributes.get("state.keys") is not None


def test_traced_span_error_records(monkeypatch):
    exporter = InMemoryExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    from src.agent import instrumentation

    def _fake_get_tracer():
        return provider.get_tracer(__name__)

    monkeypatch.setattr(instrumentation, "get_tracer", _fake_get_tracer)

    @traced_span("failing.agent")
    def failing(_: dict):
        raise ValueError("forced failure")

    try:
        failing({})
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError not raised")

    assert len(exporter.spans) >= 1
    span = exporter.spans[-1]
    assert span.name == "failing.agent"
    assert span.status.is_ok is False
    # Exception event recorded
    event_names = [e.name for e in span.events]
    assert any("exception" in n for n in event_names)
