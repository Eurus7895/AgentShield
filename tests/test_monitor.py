"""Tests for agentshield.engine.monitor.SessionMonitor."""

from __future__ import annotations

import threading
import time

import pytest

from agentshield.engine.core import SessionMonitorProtocol, ToolEvent
from agentshield.engine.monitor import SessionMonitor


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


# ---------------------------------------------------------------------------
# Protocol conformance + defaults
# ---------------------------------------------------------------------------


class TestBasics:
    def test_protocol_conformance(self):
        assert isinstance(SessionMonitor(), SessionMonitorProtocol)

    def test_single_call_allowed(self):
        m = SessionMonitor()
        assert m.check(make_event()) is None


# ---------------------------------------------------------------------------
# Loop detection
# ---------------------------------------------------------------------------


class TestLoopDetection:
    def test_exceeds_threshold_blocks(self):
        m = SessionMonitor(loop_threshold=5, loop_window_seconds=10)
        # First 4 calls allowed
        for _ in range(4):
            assert m.check(make_event()) is None
        # 5th call triggers block
        decision = m.check(make_event())
        assert decision is not None
        assert decision.is_blocked
        assert decision.reason == "session_loop"

    def test_different_tools_tracked_separately(self):
        m = SessionMonitor(loop_threshold=3, loop_window_seconds=10)
        for _ in range(2):
            assert m.check(make_event(tool_name="bash")) is None
            assert m.check(make_event(tool_name="read")) is None
        # Neither has hit threshold yet
        assert m.check(make_event(tool_name="bash")) is not None  # hits 3

    def test_different_sessions_isolated(self):
        m = SessionMonitor(loop_threshold=3, loop_window_seconds=10)
        # Session A fills up
        for _ in range(2):
            assert m.check(make_event(session_id="A")) is None
        # Session B still clean
        assert m.check(make_event(session_id="B")) is None
        # Session A hits 3 and blocks
        assert m.check(make_event(session_id="A")) is not None
        # Session B still unaffected
        assert m.check(make_event(session_id="B")) is None

    def test_window_prunes_old_events(self):
        m = SessionMonitor(loop_threshold=3, loop_window_seconds=0.05)
        # Fire 2 calls, wait for window to expire, fire 2 more → no block
        assert m.check(make_event()) is None
        assert m.check(make_event()) is None
        time.sleep(0.1)
        assert m.check(make_event()) is None
        assert m.check(make_event()) is None

    def test_block_resets_window(self):
        m = SessionMonitor(loop_threshold=3, loop_window_seconds=10)
        for _ in range(2):
            m.check(make_event())
        # Third call triggers block; after block, window cleared, so next 2 OK again.
        assert m.check(make_event()) is not None
        assert m.check(make_event()) is None
        assert m.check(make_event()) is None


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_close_session_forgets_state(self):
        m = SessionMonitor(loop_threshold=3, loop_window_seconds=10)
        m.check(make_event(session_id="X"))
        m.check(make_event(session_id="X"))
        assert m.total_for("X") == 2
        m.close_session("X")
        assert m.total_for("X") == 0
        # Subsequent call starts from zero.
        assert m.check(make_event(session_id="X")) is None

    def test_soft_cap_does_not_block(self, caplog):
        m = SessionMonitor(
            loop_threshold=1000, loop_window_seconds=60, soft_session_cap=5
        )
        for _ in range(5):
            assert m.check(make_event()) is None
        # 5th call should log warning; no decision returned.


# ---------------------------------------------------------------------------
# Thread safety — same pattern as test_engine.py
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_checks_do_not_crash(self):
        m = SessionMonitor(loop_threshold=100, loop_window_seconds=60)
        errors: list[Exception] = []
        barrier = threading.Barrier(20)

        def worker(tid: int) -> None:
            try:
                barrier.wait()
                for i in range(50):
                    m.check(
                        make_event(
                            session_id=f"sess-{tid % 3}",
                            tool_name=f"tool-{i % 4}",
                        )
                    )
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # 20 threads * 50 calls = 1000 total across 3 sessions
        totals = sum(m.total_for(f"sess-{i}") for i in range(3))
        assert totals == 1000
