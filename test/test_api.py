import os
import pytest
from httpx import AsyncClient, ASGITransport

# Enable in-memory exporter and disable real API calls for fast tests
os.environ["OTEL_INMEMORY_EXPORTER"] = "1"
os.environ.pop("AZURE_OPENAI_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GITHUB_MODELS_API_KEY", None)

from src.agent.app import app  # noqa: E402
from src.agent import instrumentation  # noqa: E402


@pytest.mark.asyncio
async def test_run_endpoint_creates_agent_spans():
    instrumentation.reset_in_memory_spans()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/run", json={"task": "Generate span test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "plan" in data and "review" in data
    spans = instrumentation.get_in_memory_spans()
    names = {s.name for s in spans}
    for expected in {"planner.agent", "worker.agent", "reflection.agent", "reviewer.agent"}:
        assert expected in names, f"Missing span {expected}; collected: {names}"
