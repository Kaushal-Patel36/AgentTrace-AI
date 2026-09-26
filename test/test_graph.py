import os

# Disable real API calls for fast tests
os.environ.pop("AZURE_OPENAI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GITHUB_MODELS_API_KEY", None)

from src.agent.graph import build_graph


def test_graph_invocation():
    graph = build_graph()
    state = {"task": "Summarize observability benefits"}
    result = graph.invoke(state)
    assert "plan" in result
    assert "work" in result
    assert "reflection" in result
    assert "review" in result
    # Ensure stub or real LLM responded
    assert isinstance(result["plan"], str)
