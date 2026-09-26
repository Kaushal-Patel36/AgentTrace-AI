"""Unit tests for SQLiteCheckpointStore and InMemoryCheckpointStore."""

import os
import tempfile
import time
from pathlib import Path

import pytest

from src.agent.persistence import (
    Checkpoint,
    InMemoryCheckpointStore,
    SQLiteCheckpointStore,
    make_run_id,
)


# ── In-memory store tests ─────────────────────────────────────────────────────

class TestInMemoryStore:
    def setup_method(self):
        self.store = InMemoryCheckpointStore()

    def _make_checkpoint(self, run_id="run-1", step="planner", task="test task"):
        return Checkpoint(
            run_id=run_id,
            step=step,
            state={"task": task, "plan": "step 1, step 2"},
            timestamp=time.time(),
            metadata={"retry_count": 0},
        )

    def test_save_and_load(self):
        cp = self._make_checkpoint()
        self.store.save(cp)
        loaded = self.store.load(cp.run_id)
        assert loaded is not None
        assert loaded.step == "planner"
        assert loaded.state["task"] == "test task"

    def test_load_by_step(self):
        self.store.save(self._make_checkpoint(step="planner"))
        self.store.save(self._make_checkpoint(step="worker"))
        loaded = self.store.load("run-1", step="planner")
        assert loaded.step == "planner"

    def test_load_returns_latest(self):
        cp1 = self._make_checkpoint(step="planner")
        cp2 = self._make_checkpoint(step="worker")
        self.store.save(cp1)
        self.store.save(cp2)
        loaded = self.store.load("run-1")
        assert loaded.step == "worker"  # latest

    def test_load_nonexistent_run_returns_none(self):
        result = self.store.load("nonexistent-run")
        assert result is None

    def test_load_nonexistent_step_returns_none(self):
        self.store.save(self._make_checkpoint(step="planner"))
        result = self.store.load("run-1", step="reviewer")
        assert result is None

    def test_list_runs(self):
        self.store.save(self._make_checkpoint(run_id="run-a"))
        self.store.save(self._make_checkpoint(run_id="run-b"))
        runs = self.store.list_runs()
        run_ids = [r["run_id"] for r in runs]
        assert "run-a" in run_ids
        assert "run-b" in run_ids

    def test_list_steps(self):
        self.store.save(self._make_checkpoint(run_id="run-1", step="planner"))
        self.store.save(self._make_checkpoint(run_id="run-1", step="worker"))
        steps = self.store.list_steps("run-1")
        assert "planner" in steps
        assert "worker" in steps

    def test_multiple_runs_isolated(self):
        self.store.save(self._make_checkpoint(run_id="run-A", step="planner"))
        self.store.save(self._make_checkpoint(run_id="run-B", step="reviewer"))
        assert self.store.load("run-A").step == "planner"
        assert self.store.load("run-B").step == "reviewer"

    def test_clear(self):
        self.store.save(self._make_checkpoint())
        self.store.clear()
        assert self.store.load("run-1") is None

    def test_list_runs_limit(self):
        for i in range(10):
            self.store.save(self._make_checkpoint(run_id=f"run-{i}"))
        runs = self.store.list_runs(limit=5)
        assert len(runs) <= 5


# ── SQLite store tests ────────────────────────────────────────────────────────

class TestSQLiteStore:
    def setup_method(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.store = SQLiteCheckpointStore(db_path=Path(self._tmp.name))

    def teardown_method(self):
        os.unlink(self._tmp.name)

    def _make_checkpoint(self, run_id="run-1", step="planner"):
        return Checkpoint(
            run_id=run_id,
            step=step,
            state={"task": "sqlite test", "plan": "a plan"},
            timestamp=time.time(),
            metadata={"source": "test"},
        )

    def test_save_and_load(self):
        cp = self._make_checkpoint()
        self.store.save(cp)
        loaded = self.store.load(cp.run_id)
        assert loaded is not None
        assert loaded.step == "planner"

    def test_state_round_trips(self):
        cp = self._make_checkpoint()
        cp.state = {"task": "hello", "plan": "step1", "nested": {"key": [1, 2, 3]}}
        self.store.save(cp)
        loaded = self.store.load("run-1")
        assert loaded.state["nested"]["key"] == [1, 2, 3]

    def test_load_by_step(self):
        self.store.save(self._make_checkpoint(step="planner"))
        self.store.save(self._make_checkpoint(step="worker"))
        loaded = self.store.load("run-1", step="planner")
        assert loaded.step == "planner"

    def test_load_returns_latest_checkpoint(self):
        self.store.save(self._make_checkpoint(step="planner"))
        self.store.save(self._make_checkpoint(step="worker"))
        loaded = self.store.load("run-1")
        assert loaded.step == "worker"

    def test_list_runs_shows_saved(self):
        self.store.save(self._make_checkpoint(run_id="run-x"))
        runs = self.store.list_runs()
        assert any(r["run_id"] == "run-x" for r in runs)

    def test_list_steps_in_order(self):
        for step in ["planner", "worker", "reflector", "reviewer"]:
            self.store.save(self._make_checkpoint(step=step))
        steps = self.store.list_steps("run-1")
        assert steps == ["planner", "worker", "reflector", "reviewer"]

    def test_complete_run_updates_status(self):
        self.store.save(self._make_checkpoint())
        self.store.complete_run("run-1", {"task": "done", "review": "great"}, "completed")
        runs = self.store.list_runs()
        run = next((r for r in runs if r["run_id"] == "run-1"), None)
        assert run is not None
        assert run["status"] == "completed"

    def test_replay_returns_all_steps(self):
        for step in ["planner", "worker"]:
            self.store.save(self._make_checkpoint(step=step))
        replayed = self.store.replay("run-1")
        assert len(replayed) == 2
        assert replayed[0].step == "planner"
        assert replayed[1].step == "worker"

    def test_nonexistent_run_returns_none(self):
        assert self.store.load("ghost-run") is None

    def test_persists_across_reconnect(self):
        """Data written by one instance is readable by a new instance."""
        self.store.save(self._make_checkpoint(run_id="persist-test"))
        store2 = SQLiteCheckpointStore(db_path=Path(self._tmp.name))
        loaded = store2.load("persist-test")
        assert loaded is not None
        assert loaded.run_id == "persist-test"


# ── make_run_id tests ─────────────────────────────────────────────────────────

def test_make_run_id_is_uuid_format():
    run_id = make_run_id()
    parts = run_id.split("-")
    assert len(parts) == 5

def test_make_run_id_is_unique():
    ids = {make_run_id() for _ in range(100)}
    assert len(ids) == 100
