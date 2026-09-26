"""Persistent checkpointing for agent workflow runs.

Saves intermediate agent state after every node, allowing:
  - Resumption of a failed run from the last good checkpoint
  - Replay of any completed run for debugging/evaluation
  - Audit trail of decision history

Backend:
  - SQLiteCheckpointStore  — local-first default (zero new dependencies)
  - InMemoryCheckpointStore — lightweight alternative for unit tests

Both implement the CheckpointStore ABC so any backend can be swapped in.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

DEFAULT_DB_PATH: Path = Path("agent_runs.db")


# ── Domain objects ────────────────────────────────────────────────────────────

@dataclass
class Checkpoint:
    """A single checkpoint snapshot captured after one agent node."""

    run_id: str
    step: str                       # e.g. "planner", "worker", "reviewer"
    state: Dict[str, Any]           # full GraphState snapshot
    timestamp: float                # Unix epoch (seconds)
    metadata: Dict[str, Any]        # arbitrary extra info (model, retry_count, …)


# ── Abstract store ────────────────────────────────────────────────────────────

class CheckpointStore(ABC):
    """Abstract interface for checkpoint persistence."""

    @abstractmethod
    def save(self, checkpoint: Checkpoint) -> None:
        """Persist a checkpoint."""

    @abstractmethod
    def load(self, run_id: str, step: Optional[str] = None) -> Optional[Checkpoint]:
        """Load the latest checkpoint for run_id (or a specific step)."""

    @abstractmethod
    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return summary rows for the most recent runs."""

    @abstractmethod
    def list_steps(self, run_id: str) -> List[str]:
        """Return all persisted step names for a run, in insertion order."""


# ── SQLite implementation ─────────────────────────────────────────────────────

class SQLiteCheckpointStore(CheckpointStore):
    """SQLite-backed checkpoint store.

    Thread-safe via WAL journal mode.  Uses Python's built-in sqlite3 — no
    extra dependencies.

    Schema:
      checkpoints : one row per (run_id, step) snapshot
      runs        : one row per run, updated on completion
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id        TEXT    NOT NULL,
                    step          TEXT    NOT NULL,
                    state_json    TEXT    NOT NULL,
                    timestamp     REAL    NOT NULL,
                    metadata_json TEXT    NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_run_id ON checkpoints(run_id)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id          TEXT PRIMARY KEY,
                    task            TEXT,
                    created_at      REAL NOT NULL,
                    completed_at    REAL,
                    status          TEXT DEFAULT 'running',
                    final_state_json TEXT
                )
            """)

    def save(self, checkpoint: Checkpoint) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO checkpoints
                       (run_id, step, state_json, timestamp, metadata_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    checkpoint.run_id,
                    checkpoint.step,
                    json.dumps(checkpoint.state, default=str),
                    checkpoint.timestamp,
                    json.dumps(checkpoint.metadata, default=str),
                ),
            )
            # Ensure a row in runs
            conn.execute(
                """INSERT INTO runs (run_id, task, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(run_id) DO NOTHING""",
                (
                    checkpoint.run_id,
                    checkpoint.state.get("task", ""),
                    checkpoint.timestamp,
                ),
            )

    def load(
        self, run_id: str, step: Optional[str] = None
    ) -> Optional[Checkpoint]:
        with self._conn() as conn:
            if step:
                row = conn.execute(
                    """SELECT * FROM checkpoints
                       WHERE run_id=? AND step=?
                       ORDER BY id DESC LIMIT 1""",
                    (run_id, step),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM checkpoints
                       WHERE run_id=?
                       ORDER BY id DESC LIMIT 1""",
                    (run_id,),
                ).fetchone()
            if not row:
                return None
            return Checkpoint(
                run_id=row["run_id"],
                step=row["step"],
                state=json.loads(row["state_json"]),
                timestamp=row["timestamp"],
                metadata=json.loads(row["metadata_json"]),
            )

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_steps(self, run_id: str) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT step FROM checkpoints WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [r["step"] for r in rows]

    def complete_run(
        self,
        run_id: str,
        final_state: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        """Mark a run as completed with its final state."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE runs
                   SET status=?, completed_at=?, final_state_json=?
                   WHERE run_id=?""",
                (status, time.time(), json.dumps(final_state, default=str), run_id),
            )

    def replay(self, run_id: str) -> List[Checkpoint]:
        """Return all checkpoints for a run in insertion order (for replay/debug)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [
                Checkpoint(
                    run_id=r["run_id"],
                    step=r["step"],
                    state=json.loads(r["state_json"]),
                    timestamp=r["timestamp"],
                    metadata=json.loads(r["metadata_json"]),
                )
                for r in rows
            ]


# ── In-memory implementation (for tests) ─────────────────────────────────────

class InMemoryCheckpointStore(CheckpointStore):
    """Non-persistent in-memory checkpoint store. Suitable for unit tests."""

    def __init__(self) -> None:
        self._data: Dict[str, List[Checkpoint]] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._data.setdefault(checkpoint.run_id, []).append(checkpoint)

    def load(
        self, run_id: str, step: Optional[str] = None
    ) -> Optional[Checkpoint]:
        items = self._data.get(run_id, [])
        if not items:
            return None
        if step:
            items = [c for c in items if c.step == step]
        return items[-1] if items else None

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [
            {"run_id": k, "steps": len(v), "task": (v[0].state.get("task", "") if v else "")}
            for k, v in list(self._data.items())[:limit]
        ]

    def list_steps(self, run_id: str) -> List[str]:
        return [c.step for c in self._data.get(run_id, [])]

    def clear(self) -> None:
        """Remove all stored data (useful between tests)."""
        self._data.clear()


# ── Global default store ──────────────────────────────────────────────────────

_default_store: Optional[SQLiteCheckpointStore] = None


def get_store() -> SQLiteCheckpointStore:
    """Return the process-level default SQLite checkpoint store."""
    global _default_store
    if _default_store is None:
        _default_store = SQLiteCheckpointStore()
    return _default_store


def make_run_id() -> str:
    """Generate a new unique run ID."""
    return str(uuid.uuid4())


__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "SQLiteCheckpointStore",
    "InMemoryCheckpointStore",
    "get_store",
    "make_run_id",
]
