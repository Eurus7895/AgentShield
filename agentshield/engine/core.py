"""AgentShield core engine — ToolEvent, EngineDecision, AgentShieldEngine.

This module is stdlib-only. No third-party imports allowed here.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_VALID_AGENT_TYPES = frozenset({"main", "subagent"})
_VALID_FRAMEWORKS = frozenset({"claude_code", "mcp", "sdk", "opensandbox"})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ToolEvent:
    """Represents a single agent tool call intercepted by AgentShield."""

    tool_name: str
    tool_input: dict
    session_id: str
    agent_id: str
    agent_type: str  # "main" | "subagent"
    framework: str   # "claude_code" | "mcp" | "sdk" | "opensandbox"
    timestamp: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if self.agent_type not in _VALID_AGENT_TYPES:
            raise ValueError(
                f"Invalid agent_type {self.agent_type!r}. "
                f"Must be one of: {sorted(_VALID_AGENT_TYPES)}"
            )
        if self.framework not in _VALID_FRAMEWORKS:
            raise ValueError(
                f"Invalid framework {self.framework!r}. "
                f"Must be one of: {sorted(_VALID_FRAMEWORKS)}"
            )
        if not self.timestamp:
            self.timestamp = _utcnow_iso()

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "framework": self.framework,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolEvent":
        return cls(
            tool_name=data["tool_name"],
            tool_input=data.get("tool_input", {}),
            session_id=data.get("session_id", ""),
            agent_id=data.get("agent_id", ""),
            agent_type=data.get("agent_type", "main"),
            framework=data.get("framework", "claude_code"),
            timestamp=data.get("timestamp", _utcnow_iso()),
        )


@dataclass
class EngineDecision:
    """Decision returned by AgentShieldEngine for a given ToolEvent."""

    action: str        # "allow" | "block"
    reason: str | None = None
    message: str | None = None

    @classmethod
    def allow(cls) -> "EngineDecision":
        return cls(action="allow")

    @classmethod
    def block(cls, reason: str, message: str | None = None) -> "EngineDecision":
        return cls(action="block", reason=reason, message=message)

    @property
    def is_blocked(self) -> bool:
        return self.action == "block"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Component protocols (dependency injection interfaces)
# ---------------------------------------------------------------------------


@runtime_checkable
class PolicyEngineProtocol(Protocol):
    """Evaluates a ToolEvent against policy rules."""

    def evaluate(self, event: ToolEvent) -> EngineDecision:
        ...


@runtime_checkable
class AuditLoggerProtocol(Protocol):
    """Persists a ToolEvent + decision to the audit log."""

    def log(
        self,
        event: ToolEvent,
        decision: EngineDecision,
        duration_ms: int,
    ) -> None:
        ...


@runtime_checkable
class OutputScannerProtocol(Protocol):
    """Scans tool output for credentials or PII."""

    def scan(self, event: ToolEvent, output: str) -> list[str]:
        ...


@runtime_checkable
class SessionMonitorProtocol(Protocol):
    """Detects anomalous session behaviour (e.g. infinite loops)."""

    def check(self, event: ToolEvent) -> EngineDecision | None:
        ...


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class AgentShieldEngine:
    """Main orchestrator: receives a ToolEvent, runs the pipeline, returns a decision.

    All components are optional — the engine is useful standalone (allow-all)
    and gains capabilities as components are injected.

    Thread-safe: a single engine instance can be shared across daemon threads.
    """

    def __init__(
        self,
        policy: PolicyEngineProtocol | None = None,
        logger_: AuditLoggerProtocol | None = None,
        scanner: OutputScannerProtocol | None = None,
        monitor: SessionMonitorProtocol | None = None,
    ) -> None:
        self._policy = policy
        self._logger = logger_
        self._scanner = scanner
        self._monitor = monitor
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # PreToolUse pipeline
    # ------------------------------------------------------------------

    def process(self, event: ToolEvent) -> EngineDecision:
        """Evaluate a PreToolUse event and return allow/block decision.

        Pipeline order:
          1. Session monitor (loop / anomaly detection) — fast-path block
          2. Policy engine (YAML rule evaluation)
          3. Audit logger (always runs, even on block)

        Fail-open: any unhandled exception → allow + log error.
        """
        start = datetime.now(timezone.utc)
        try:
            with self._lock:
                decision = self._run_pipeline(event, start)
        except Exception:
            logger.exception("AgentShieldEngine.process raised; failing open")
            decision = EngineDecision.allow()
        return decision

    def _run_pipeline(
        self, event: ToolEvent, start: datetime
    ) -> EngineDecision:
        # 1. Session monitor — early exit on anomaly
        if self._monitor is not None:
            try:
                monitor_decision = self._monitor.check(event)
                if monitor_decision is not None and monitor_decision.is_blocked:
                    self._audit(event, monitor_decision, start)
                    return monitor_decision
            except Exception:
                logger.exception("SessionMonitor.check raised; skipping")

        # 2. Policy evaluation
        decision = EngineDecision.allow()
        if self._policy is not None:
            try:
                decision = self._policy.evaluate(event)
            except Exception:
                logger.exception("PolicyEngine.evaluate raised; defaulting to allow")
                decision = EngineDecision.allow()

        # 3. Audit log (best-effort)
        self._audit(event, decision, start)

        return decision

    def _audit(
        self, event: ToolEvent, decision: EngineDecision, start: datetime
    ) -> None:
        if self._logger is None:
            return
        try:
            elapsed = datetime.now(timezone.utc) - start
            duration_ms = int(elapsed.total_seconds() * 1000)
            self._logger.log(event, decision, duration_ms)
        except Exception:
            logger.exception("AuditLogger.log raised; ignoring")

    # ------------------------------------------------------------------
    # PostToolUse pipeline
    # ------------------------------------------------------------------

    def process_post_tool(self, event: ToolEvent, output: str) -> list[str]:
        """Scan tool output for credential / PII leaks.

        Returns a list of finding strings (empty list = nothing found).
        Fail-open: any exception → [].
        """
        if self._scanner is None:
            return []
        try:
            return self._scanner.scan(event, output)
        except Exception:
            logger.exception("OutputScanner.scan raised; returning no findings")
            return []
