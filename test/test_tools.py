"""Unit tests for the tool registry and built-in tools."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ["OTEL_INMEMORY_EXPORTER"] = "1"

from src.agent.tools import (
    ToolRegistry,
    ToolResult,
    calculator_tool,
    get_registry,
    retrieval_tool,
)


# ── Calculator tests ──────────────────────────────────────────────────────────

class TestCalculatorTool:
    def test_basic_addition(self):
        result = calculator_tool({"expression": "2 + 2"})
        assert result["result"] == 4

    def test_multiplication(self):
        result = calculator_tool({"expression": "7 * 8"})
        assert result["result"] == 56

    def test_float_arithmetic(self):
        result = calculator_tool({"expression": "100 * 0.15"})
        assert abs(result["result"] - 15.0) < 0.001

    def test_percentage_calc(self):
        result = calculator_tool({"expression": "0.15 * 450"})
        assert abs(result["result"] - 67.5) < 0.001

    def test_sqrt(self):
        result = calculator_tool({"expression": "sqrt(144)"})
        assert abs(result["result"] - 12.0) < 0.001

    def test_power(self):
        result = calculator_tool({"expression": "2 ** 10"})
        assert result["result"] == 1024

    def test_compound_expression(self):
        result = calculator_tool({"expression": "2 ** 10 + 100 / 4"})
        assert result["result"] == 1049.0

    def test_modulo(self):
        result = calculator_tool({"expression": "17 % 5"})
        assert result["result"] == 2

    def test_nested_functions(self):
        result = calculator_tool({"expression": "round(sqrt(2), 4)"})
        assert abs(result["result"] - 1.4142) < 0.001

    def test_returns_expression_echo(self):
        result = calculator_tool({"expression": "3 + 4"})
        assert result["expression"] == "3 + 4"

    def test_empty_expression_raises(self):
        with pytest.raises(ValueError, match="No expression"):
            calculator_tool({"expression": ""})

    def test_missing_expression_raises(self):
        with pytest.raises(ValueError, match="No expression"):
            calculator_tool({})

    def test_too_long_expression_raises(self):
        with pytest.raises(ValueError, match="too long"):
            calculator_tool({"expression": "1" * 501})

    def test_disallows_import(self):
        with pytest.raises(ValueError):
            calculator_tool({"expression": "__import__('os').system('ls')"})

    def test_disallows_attribute_access(self):
        with pytest.raises(ValueError):
            calculator_tool({"expression": "os.system('ls')"})

    def test_invalid_syntax_raises(self):
        with pytest.raises(ValueError, match="Invalid expression"):
            calculator_tool({"expression": "2 +"})

    def test_floor_division(self):
        result = calculator_tool({"expression": "17 // 5"})
        assert result["result"] == 3

    def test_pi_constant(self):
        result = calculator_tool({"expression": "pi"})
        assert abs(result["result"] - 3.14159) < 0.001

    def test_math_chain(self):
        # 2025 tokens / day * 30 days * $0.001/1k = $0.0607 (approx)
        result = calculator_tool({"expression": "2025 * 30 * 0.001 / 1000"})
        assert result["result"] > 0


# ── Retrieval tests ───────────────────────────────────────────────────────────

class TestRetrievalTool:
    def test_basic_retrieval(self):
        result = retrieval_tool({"query": "opentelemetry"})
        assert result["query"] == "opentelemetry"
        assert isinstance(result["results"], list)
        assert len(result["results"]) >= 1

    def test_returns_relevant_result(self):
        result = retrieval_tool({"query": "what is langgraph"})
        sources = [r["source"] for r in result["results"]]
        assert "langgraph" in sources

    def test_returns_top3_max(self):
        result = retrieval_tool({"query": "observability telemetry monitoring"})
        assert len(result["results"]) <= 3

    def test_result_has_required_fields(self):
        result = retrieval_tool({"query": "llm"})
        for item in result["results"]:
            assert "source" in item
            assert "content" in item
            assert "score" in item
            assert isinstance(item["score"], float)

    def test_no_match_returns_fallback(self):
        result = retrieval_tool({"query": "quantum entanglement photon collider"})
        assert len(result["results"]) >= 1

    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="No query"):
            retrieval_tool({"query": ""})

    def test_missing_query_raises(self):
        with pytest.raises(ValueError, match="No query"):
            retrieval_tool({})

    def test_total_found_field(self):
        result = retrieval_tool({"query": "llm agent"})
        assert "total_found" in result
        assert isinstance(result["total_found"], int)


# ── ToolRegistry tests ────────────────────────────────────────────────────────

class TestToolRegistry:
    def test_list_tools(self):
        registry = get_registry()
        tools = registry.list_tools()
        names = [t["name"] for t in tools]
        assert "calculator" in names
        assert "retrieval" in names

    def test_call_calculator_via_registry(self):
        registry = get_registry()
        result = registry.call("calculator", {"expression": "5 * 5"})
        assert isinstance(result, ToolResult)
        assert result.success
        assert result.output["result"] == 25

    def test_call_retrieval_via_registry(self):
        registry = get_registry()
        result = registry.call("retrieval", {"query": "fastapi"})
        assert result.success
        assert isinstance(result.output["results"], list)

    def test_unknown_tool_returns_failure(self):
        registry = get_registry()
        result = registry.call("nonexistent_tool", {})
        assert not result.success
        assert "Unknown tool" in result.error

    def test_tool_exception_returns_failure(self):
        registry = get_registry()
        result = registry.call("calculator", {"expression": "2 +"})
        assert not result.success
        assert result.error is not None

    def test_result_has_latency(self):
        registry = get_registry()
        result = registry.call("calculator", {"expression": "1 + 1"})
        assert result.latency_ms >= 0.0

    def test_custom_tool_registration(self):
        registry = ToolRegistry()
        registry.register(
            "echo",
            lambda inp: {"echoed": inp.get("text", "")},
            schema={"name": "echo", "description": "Echo input"},
        )
        result = registry.call("echo", {"text": "hello"})
        assert result.success
        assert result.output["echoed"] == "hello"

    def test_tool_result_to_dict(self):
        r = ToolResult(tool_name="calc", success=True, output={"result": 4}, latency_ms=12.5)
        d = r.to_dict()
        assert d["tool_name"] == "calc"
        assert d["success"] is True
        assert d["latency_ms"] == 12.5
