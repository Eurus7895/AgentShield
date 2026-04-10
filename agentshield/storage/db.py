"""SQLite audit logger — WAL mode, deduplicating, provenance-ready.

Conforms to AuditLoggerProtocol from agentshield.engine.core.

Design notes:
  * WAL mode: enables concurrent dashboard reads while the hook writes.
  * INSERT OR IGNORE on a unique composite index prevents double-logging when
    multiple adapters fire on the same event.
  * Thread-safe: every public method opens its own short-lived connection.
    sqlite3 connections cannot be shared across threads, but the underlying
    database file is safe for concurrent access in WAL mode.
  * source_event_id + provenance_tags columns exist from v1 so the Month 2
    linking logic lands without migration.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agentshield.engine.core import EngineDecision, ToolEvent

logger = logging.getLogger(__name__)

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class AuditLogger:
    """SQLite-backed audit logger implementing AuditLoggerProtocol."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialize()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _initialize(self) -> None:
        with self._init_lock, self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            # WAL + synchronous=NORMAL is the standard fast-but-safe combo.
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self._path),
            isolation_level=None,  # autocommit off; we manage transactions
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # AuditLoggerProtocol
    # ------------------------------------------------------------------

    def log(
        self,
        event: ToolEvent,
        decision: EngineDecision,
        duration_ms: int,
    ) -> None:
        """Insert a tool_calls row. Deduplicated by composite unique index.

        Fail-soft: exceptions are logged but never raised. The engine catches
        them too, but defense-in-depth keeps a broken DB from crashing hooks.
        """
        try:
            with self._connect() as conn:
                try:
                    serialized_input = json.dumps(
                        event.tool_input, sort_keys=True, default=str
                    )
                except (TypeError, ValueError):
                    serialized_input = str(event.tool_input)

                conn.execute(
                    """
                    INSERT OR IGNORE INTO tool_calls (
                        ts, session_id, agent_id, agent_type, framework,
                        tool, input, blocked, reason, message, duration_ms,
                        source_event_id, provenance_tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.timestamp,
                        event.session_id,
                        event.agent_id,
                        event.agent_type,
                        event.framework,
                        event.tool_name,
                        serialized_input,
                        1 if decision.is_blocked else 0,
                        decision.reason,
                        decision.message,
                        duration_ms,
                        None,  # source_event_id populated by Month 2 linker
                        None,  # provenance_tags populated by Month 2 linker
                    ),
                )
                # Upsert the session row.
                conn.execute(
                    """
                    INSERT INTO sessions (id, agent_id, framework, started_at, tool_count, block_count)
                    VALUES (?, ?, ?, ?, 1, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        tool_count = tool_count + 1,
                        block_count = block_count + excluded.block_count
                    """,
                    (
                        event.session_id,
                        event.agent_id,
                        event.framework,
                        event.timestamp,
                        1 if decision.is_blocked else 0,
                    ),
                )
                conn.commit()
        except Exception:
            logger.exception("AuditLogger.log failed; swallowing")

    # ------------------------------------------------------------------
    # Query helpers (used by dashboard, CLI, tests)
    # ------------------------------------------------------------------

    def count_calls(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM tool_calls").fetchone()
            return int(row["c"]) if row else 0

    def recent(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def by_session(self, session_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Provenance linking helpers (schema-ready; logic lands Month 2)
    # ------------------------------------------------------------------

    def link_source(self, write_id: int, read_id: int) -> None:
        """Set source_event_id on a write row to point at the source read."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tool_calls SET source_event_id = ? WHERE id = ?",
                (read_id, write_id),
            )
            conn.commit()

    def set_provenance_tags(self, event_id: int, tags: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tool_calls SET provenance_tags = ? WHERE id = ?",
                (json.dumps(tags), event_id),
            )
            conn.commit()

    def trace_provenance(self, event_id: int) -> list[dict]:
        """Walk the source_event_id chain back from event_id to the origin.

        Returns the ancestor chain (oldest first). In Week 1, this is only
        exercised when provenance linking has been populated manually.
        """
        result: list[dict] = []
        visited: set[int] = set()
        current = event_id
        with self._connect() as conn:
            while current is not None and current not in visited:
                visited.add(current)
                row = conn.execute(
                    "SELECT * FROM tool_calls WHERE id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    break
                result.append(dict(row))
                current = row["source_event_id"]
        return list(reversed(result))

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def journal_mode(self) -> str:
        with self._connect() as conn:
            row = conn.execute("PRAGMA journal_mode;").fetchone()
            return (row[0] if row else "").lower()

    def columns(self, table: str = "tool_calls") -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
            return [r["name"] for r in rows]
