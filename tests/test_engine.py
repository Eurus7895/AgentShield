"""Tests for agentshield.engine.core."""

import threading
from unittest.mock import MagicMock

import pytest

from agentshield.engine.core import (
    AgentShieldEngine,
    AuditLoggerProtocol,
    EngineDecision,
    OutputScannerProtocol,
    PolicyEngineProtocol,
    SessionMonitorProtocol,
    ToolEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(**kwargs) -> ToolEvent:
    defaults = dict(
        tool_name="bash",
        tool_input={"command": "ls"},
        session_id="sess-123",
        agent_id="agent-abc",
        agent_type="main",
        framework="claude_code",
    )
    defaults.update(kwargs)
    return ToolEvent(**defaults)


# ---------------------------------------------------------------------------
# ToolEvent
# ---------------------------------------------------------------------------

class TestToolEvent:
    def test_basic_creation(self):
        e = make_event()
        assert e.tool_name == "bash"
        assert e.tool_input == {"command": "ls"}
        assert e.agent_type == "main"
        assert e.framework == "claude_code"
        assert e.timestamp  # auto-set

    def test_explicit_timestamp(self):
        e = make_event(timestamp="2026-01-01T00:00:00Z")
        assert e.timestamp == "2026-01-01T00:00:00Z"

    def test_empty_timestamp_gets_filled(self):
        e = make_event(timestamp="")
        assert e.timestamp  # filled by __post_init__

    def test_invalid_agent_type(self):
        with pytest.raises(ValueError, match="agent_type"):
            make_event(agent_type="robot")

    def test_invalid_framework(self):
        with pytest.raises(ValueError, match="framework"):
            make_event(framework="langchain")

    def test_valid_agent_types(self):
        for at in ("main", "subagent"):
            e = make_event(agent_type=at)
            assert e.agent_type == at

    def test_valid_frameworks(self):
        for fw in ("claude_code", "mcp", "sdk", "opensandbox"):
            e = make_event(framework=fw)
            assert e.framework == fw

    def test_to_dict_round_trip(self):
        e = make_event()
        d = e.to_dict()
        e2 = ToolEvent.from_dict(d)
        assert e2.tool_name == e.tool_name
        assert e2.tool_input == e.tool_input
        assert e2.session_id == e.session_id
        assert e2.agent_id == e.agent_id
        assert e2.agent_type == e.agent_type
        assert e2.framework == e.framework
        assert e2.timestamp == e.timestamp

    def test_from_dict_defaults(self):
        e = ToolEvent.from_dict({"tool_name": "read", "tool_input": {}})
        assert e.agent_type == "main"
        assert e.framework == "claude_code"
        assert e.timestamp


# ---------------------------------------------------------------------------
# EngineDecision
# ---------------------------------------------------------------------------

class TestEngineDecision:
    def test_allow_factory(self):
        d = EngineDecision.allow()
        assert d.action == "allow"
        assert d.reason is None
        assert d.message is None
        assert not d.is_blocked

    def test_block_factory(self):
        d = EngineDecision.block("block_rm_rf", "Dangerous deletion blocked")
        assert d.action == "block"
        assert d.reason == "block_rm_rf"
        assert d.message == "Dangerous deletion blocked"
        assert d.is_blocked

    def test_block_without_message(self):
        d = EngineDecision.block("protect_ssh")
        assert d.message is None

    def test_to_dict(self):
        d = EngineDecision.block("rule", "msg")
        data = d.to_dict()
        assert data == {"action": "block", "reason": "rule", "message": "msg"}


# ---------------------------------------------------------------------------
# AgentShieldEngine — no components (default allow)
# ---------------------------------------------------------------------------

class TestEngineNoComponents:
    def test_process_allows_by_default(self):
        engine = AgentShieldEngine()
        decision = engine.process(make_event())
        assert decision.action == "allow"

    def test_process_post_tool_returns_empty(self):
        engine = AgentShieldEngine()
        findings = engine.process_post_tool(make_event(), "output text")
        assert findings == []


# ---------------------------------------------------------------------------
# AgentShieldEngine — dependency injection with mocks
# ---------------------------------------------------------------------------

class TestEngineMockPolicy:
    def test_policy_block_propagates(self):
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.return_value = EngineDecision.block("test_rule", "blocked")

        engine = AgentShieldEngine(policy=policy)
        decision = engine.process(make_event())

        assert decision.is_blocked
        assert decision.reason == "test_rule"
        policy.evaluate.assert_called_once()

    def test_policy_allow_propagates(self):
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.return_value = EngineDecision.allow()

        engine = AgentShieldEngine(policy=policy)
        decision = engine.process(make_event())
        assert not decision.is_blocked

    def test_audit_logger_called_on_allow(self):
        audit = MagicMock(spec=AuditLoggerProtocol)
        engine = AgentShieldEngine(logger_=audit)
        engine.process(make_event())
        audit.log.assert_called_once()

    def test_audit_logger_called_on_block(self):
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.return_value = EngineDecision.block("r")
        audit = MagicMock(spec=AuditLoggerProtocol)

        engine = AgentShieldEngine(policy=policy, logger_=audit)
        engine.process(make_event())
        audit.log.assert_called_once()

    def test_monitor_block_skips_policy(self):
        monitor = MagicMock(spec=SessionMonitorProtocol)
        monitor.check.return_value = EngineDecision.block("loop_detected")
        policy = MagicMock(spec=PolicyEngineProtocol)

        engine = AgentShieldEngine(policy=policy, monitor=monitor)
        decision = engine.process(make_event())

        assert decision.is_blocked
        assert decision.reason == "loop_detected"
        policy.evaluate.assert_not_called()

    def test_monitor_none_return_continues_pipeline(self):
        monitor = MagicMock(spec=SessionMonitorProtocol)
        monitor.check.return_value = None
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.return_value = EngineDecision.allow()

        engine = AgentShieldEngine(policy=policy, monitor=monitor)
        decision = engine.process(make_event())
        assert not decision.is_blocked
        policy.evaluate.assert_called_once()

    def test_scanner_findings_returned(self):
        scanner = MagicMock(spec=OutputScannerProtocol)
        scanner.scan.return_value = ["AWS_SECRET_KEY found"]

        engine = AgentShieldEngine(scanner=scanner)
        findings = engine.process_post_tool(make_event(), "AWS_SECRET_KEY=abc123")
        assert findings == ["AWS_SECRET_KEY found"]


# ---------------------------------------------------------------------------
# Fault tolerance (fail-open)
# ---------------------------------------------------------------------------

class TestFaultTolerance:
    def test_policy_exception_fails_open(self):
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.side_effect = RuntimeError("DB is down")

        engine = AgentShieldEngine(policy=policy)
        decision = engine.process(make_event())
        assert decision.action == "allow"

    def test_monitor_exception_continues(self):
        monitor = MagicMock(spec=SessionMonitorProtocol)
        monitor.check.side_effect = RuntimeError("monitor exploded")
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.return_value = EngineDecision.allow()

        engine = AgentShieldEngine(policy=policy, monitor=monitor)
        decision = engine.process(make_event())
        assert decision.action == "allow"
        policy.evaluate.assert_called_once()

    def test_logger_exception_does_not_propagate(self):
        audit = MagicMock(spec=AuditLoggerProtocol)
        audit.log.side_effect = IOError("disk full")

        engine = AgentShieldEngine(logger_=audit)
        decision = engine.process(make_event())
        assert decision.action == "allow"

    def test_scanner_exception_returns_empty(self):
        scanner = MagicMock(spec=OutputScannerProtocol)
        scanner.scan.side_effect = RuntimeError("scanner broke")

        engine = AgentShieldEngine(scanner=scanner)
        findings = engine.process_post_tool(make_event(), "output")
        assert findings == []


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_policy_protocol(self):
        class AlwaysAllow:
            def evaluate(self, event: ToolEvent) -> EngineDecision:
                return EngineDecision.allow()

        assert isinstance(AlwaysAllow(), PolicyEngineProtocol)

    def test_logger_protocol(self):
        class NoopLogger:
            def log(self, event, decision, duration_ms):
                pass

        assert isinstance(NoopLogger(), AuditLoggerProtocol)

    def test_scanner_protocol(self):
        class NoopScanner:
            def scan(self, event, output):
                return []

        assert isinstance(NoopScanner(), OutputScannerProtocol)

    def test_monitor_protocol(self):
        class NoopMonitor:
            def check(self, event):
                return None

        assert isinstance(NoopMonitor(), SessionMonitorProtocol)


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_process(self):
        policy = MagicMock(spec=PolicyEngineProtocol)
        policy.evaluate.return_value = EngineDecision.allow()
        engine = AgentShieldEngine(policy=policy)

        results = []
        errors = []

        def worker():
            try:
                d = engine.process(make_event(session_id=f"sess-{threading.get_ident()}"))
                results.append(d.action)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(r == "allow" for r in results)
        assert len(results) == 20
