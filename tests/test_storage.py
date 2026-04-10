"""Tests for agentshield.storage.db.AuditLogger."""

from __future__ import annotations

import threading

import pytest

from agentshield.engine.core import AuditLoggerProtocol, EngineDecision, ToolEvent
from agentshield.storage.db import AuditLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_event(**kwargs) -> ToolEvent:
    defaults = dict(
        tool_name="bash",
        tool_input={"command": "ls"},
        session_id="sess-1",
        agent_id="main",
        agent_type="main",
        framework="claude_code",
    )
    defaults.update(kwargs)
    return ToolEvent(**defaults)


@pytest.fixture
def logger(tmp_path) -> AuditLogger:
    return AuditLogger(tmp_path / "logs.db")


# ---------------------------------------------------------------------------
# Schema + bootstrapping
# ---------------------------------------------------------------------------


class TestSchema:
    def test_protocol_conformance(self, logger):
        assert isinstance(logger, AuditLoggerProtocol)

    def test_wal_mode_enabled(self, logger):
        assert logger.journal_mode() == "wal"

    def test_provenance_columns_present(self, logger):
        cols = logger.columns("tool_calls")
        assert "source_event_id" in cols
        assert "provenance_tags" in cols

    def test_harness_columns_present(self, logger):
        cols = logger.columns("tool_calls")
        for required in (
            "session_id",
            "agent_id",
            "agent_type",
            "framework",
            "blocked",
            "reason",
            "duration_ms",
        ):
            assert required in cols

    def test_sessions_table_present(self, logger):
        cols = logger.columns("sessions")
        assert "id" in cols
        assert "tool_count" in cols
        assert "block_count" in cols


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLog:
    def test_allow_event_logged(self, logger):
        logger.log(make_event(), EngineDecision.allow(), 3)
        assert logger.count_calls() == 1
        rows = logger.recent()
        assert rows[0]["blocked"] == 0
        assert rows[0]["tool"] == "bash"
        assert rows[0]["duration_ms"] == 3

    def test_block_event_logged_with_reason(self, logger):
        decision = EngineDecision.block("block_rm_rf", "nope")
        logger.log(make_event(tool_input={"command": "rm -rf /"}), decision, 5)
        rows = logger.recent()
        assert rows[0]["blocked"] == 1
        assert rows[0]["reason"] == "block_rm_rf"
        assert rows[0]["message"] == "nope"

    def test_dedup_via_unique_index(self, logger):
        event = make_event(timestamp="2026-04-09T00:00:00+00:00")
        for _ in range(5):
            logger.log(event, EngineDecision.allow(), 1)
        assert logger.count_calls() == 1

    def test_distinct_events_not_deduped(self, logger):
        logger.log(
            make_event(timestamp="2026-04-09T00:00:00+00:00"),
            EngineDecision.allow(),
            1,
        )
        logger.log(
            make_event(timestamp="2026-04-09T00:00:01+00:00"),
            EngineDecision.allow(),
            1,
        )
        assert logger.count_calls() == 2

    def test_harness_fields_populated(self, logger):
        logger.log(
            make_event(
                session_id="s1",
                agent_id="generator-1",
                agent_type="subagent",
            ),
            EngineDecision.allow(),
            1,
        )
        row = logger.recent()[0]
        assert row["session_id"] == "s1"
        assert row["agent_id"] == "generator-1"
        assert row["agent_type"] == "subagent"
        assert row["framework"] == "claude_code"

    def test_tool_input_serialized(self, logger):
        logger.log(
            make_event(tool_input={"command": "echo", "args": ["a", "b"]}),
            EngineDecision.allow(),
            1,
        )
        row = logger.recent()[0]
        assert "echo" in row["input"]
        assert "args" in row["input"]

    def test_fails_soft_on_bad_input(self, logger):
        # Non-JSON-serializable object should not raise.
        class Weird:
            pass

        logger.log(
            make_event(tool_input={"weird": Weird()}),
            EngineDecision.allow(),
            1,
        )
        assert logger.count_calls() == 1


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------


class TestSessions:
    def test_session_counts_increment(self, logger):
        event = make_event(session_id="sX")
        logger.log(event, EngineDecision.allow(), 1)
        logger.log(
            make_event(session_id="sX", timestamp="2026-04-09T00:00:01+00:00"),
            EngineDecision.block("r", "m"),
            1,
        )
        logger.log(
            make_event(session_id="sX", timestamp="2026-04-09T00:00:02+00:00"),
            EngineDecision.allow(),
            1,
        )
        with logger._connect() as conn:
            row = conn.execute(
                "SELECT tool_count, block_count FROM sessions WHERE id = ?",
                ("sX",),
            ).fetchone()
        assert row["tool_count"] == 3
        assert row["block_count"] == 1

    def test_by_session_query(self, logger):
        for i in range(3):
            logger.log(
                make_event(
                    session_id="sY", timestamp=f"2026-04-09T00:00:0{i}+00:00"
                ),
                EngineDecision.allow(),
                1,
            )
        rows = logger.by_session("sY")
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# Provenance helpers (schema-ready)
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_link_and_trace(self, logger):
        # Insert a read, then a write, then link them.
        logger.log(
            make_event(
                tool_name="read",
                tool_input={"file_path": "note.txt"},
                timestamp="2026-04-09T00:00:00+00:00",
            ),
            EngineDecision.allow(),
            1,
        )
        logger.log(
            make_event(
                tool_name="write",
                tool_input={"file_path": "copy.txt", "content": "x"},
                timestamp="2026-04-09T00:00:01+00:00",
            ),
            EngineDecision.allow(),
            1,
        )
        # Link write (id 2) back to read (id 1)
        logger.link_source(write_id=2, read_id=1)
        chain = logger.trace_provenance(2)
        # Chain should be [read, write] (oldest first)
        assert [c["tool"] for c in chain] == ["read", "write"]

    def test_set_provenance_tags(self, logger):
        logger.log(make_event(), EngineDecision.allow(), 1)
        logger.set_provenance_tags(1, ["external", "web_fetch"])
        row = logger.recent()[0]
        assert "external" in row["provenance_tags"]
        assert "web_fetch" in row["provenance_tags"]


# ---------------------------------------------------------------------------
# Concurrency — WAL mode allows parallel writes
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_writes_no_crash(self, tmp_path):
        logger_ = AuditLogger(tmp_path / "conc.db")

        def worker(tid: int) -> None:
            for i in range(20):
                logger_.log(
                    make_event(
                        session_id=f"s{tid}",
                        timestamp=f"2026-04-09T00:00:{i:02d}+00:00",
                    ),
                    EngineDecision.allow(),
                    1,
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert logger_.count_calls() == 200
