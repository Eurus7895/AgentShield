"""Policy engine — YAML rule evaluation with first-match-wins semantics.

Conforms to PolicyEngineProtocol from agentshield.engine.core.

Rule fields:
    tool:       str | list[str] | "*"          — tool name matcher
    match:      str | list[str]                — substring(s) in serialized tool_input
    path_match: str | list[str]                — glob(s) applied to path-like fields
    agent_id:   str | list[str]                — per-agent matcher (supports fnmatch wildcards)
    agent_type: str | list[str]                — "main" | "subagent" matcher
    action:     "allow" | "deny"
    message:    str                            — shown to agent on block
    priority:   int                            — sort desc; first match wins
    name:       str                            — rule identifier (used as block reason)

Evaluation: sort rules by priority desc, first match wins, default allow.
Hot-reload is triggered externally (the daemon calls reload_from_path()).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agentshield.engine.core import EngineDecision, ToolEvent

logger = logging.getLogger(__name__)

# Fields inside tool_input that are treated as "paths" for path_match evaluation.
_PATH_FIELDS = ("file_path", "path", "notebook_path", "target", "source")


@dataclass
class PolicyRule:
    """A single compiled policy rule."""

    name: str
    action: str
    tool: tuple[str, ...] = field(default_factory=tuple)  # empty = match any
    match: tuple[str, ...] = field(default_factory=tuple)
    path_match: tuple[str, ...] = field(default_factory=tuple)
    agent_id: tuple[str, ...] = field(default_factory=tuple)
    agent_type: tuple[str, ...] = field(default_factory=tuple)
    message: str | None = None
    priority: int = 0

    def matches(self, event: ToolEvent) -> bool:
        if self.tool and not self._tool_matches(event.tool_name):
            return False
        if self.agent_id and not self._agent_id_matches(event.agent_id):
            return False
        if self.agent_type and event.agent_type not in self.agent_type:
            return False
        if self.match and not self._substring_matches(event.tool_input):
            return False
        if self.path_match and not self._path_matches(event.tool_input):
            return False
        return True

    def _tool_matches(self, tool_name: str) -> bool:
        if "*" in self.tool:
            return True
        return tool_name in self.tool

    def _agent_id_matches(self, agent_id: str) -> bool:
        for pattern in self.agent_id:
            if fnmatch.fnmatchcase(agent_id, pattern):
                return True
        return False

    def _substring_matches(self, tool_input: dict) -> bool:
        try:
            serialized = json.dumps(tool_input, sort_keys=True)
        except (TypeError, ValueError):
            serialized = str(tool_input)
        return any(needle in serialized for needle in self.match)

    def _path_matches(self, tool_input: dict) -> bool:
        candidates: list[str] = []
        for key in _PATH_FIELDS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
        if not candidates:
            return False
        for pattern in self.path_match:
            # Match if the pattern appears as a substring OR as a glob match.
            for candidate in candidates:
                if pattern in candidate:
                    return True
                if fnmatch.fnmatchcase(candidate, pattern):
                    return True
                # Also match against the basename for bare filename patterns.
                basename = candidate.rsplit("/", 1)[-1]
                if fnmatch.fnmatchcase(basename, pattern):
                    return True
        return False


def _to_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a scalar/list rule field to a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def compile_rule(raw: dict) -> PolicyRule:
    """Build a PolicyRule from its YAML dict form."""
    name = raw.get("name") or "unnamed_rule"
    action = raw.get("action", "allow")
    if action not in ("allow", "deny"):
        raise ValueError(f"Rule {name!r}: action must be 'allow' or 'deny', got {action!r}")
    return PolicyRule(
        name=name,
        action=action,
        tool=_to_tuple(raw.get("tool")),
        match=_to_tuple(raw.get("match")),
        path_match=_to_tuple(raw.get("path_match")),
        agent_id=_to_tuple(raw.get("agent_id")),
        agent_type=_to_tuple(raw.get("agent_type")),
        message=raw.get("message"),
        priority=int(raw.get("priority", 0)),
    )


def compile_rules(raw_rules: list[dict]) -> list[PolicyRule]:
    """Compile a list of raw YAML rule dicts into sorted PolicyRule objects."""
    compiled = [compile_rule(r) for r in raw_rules]
    # Stable sort by priority desc so ties preserve source order.
    compiled.sort(key=lambda r: -r.priority)
    return compiled


class PolicyEngine:
    """Thread-safe PolicyEngine implementing PolicyEngineProtocol."""

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules: list[PolicyRule] = list(rules) if rules else []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_text: str) -> "PolicyEngine":
        data = yaml.safe_load(yaml_text) or {}
        raw_rules = data.get("rules", []) or []
        return cls(compile_rules(raw_rules))

    @classmethod
    def from_path(cls, path: str | Path) -> "PolicyEngine":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

    def reload_from_path(self, path: str | Path) -> None:
        """Atomically replace the active rule set with rules parsed from path."""
        new_engine = PolicyEngine.from_path(path)
        with self._lock:
            self._rules = new_engine._rules

    # ------------------------------------------------------------------
    # PolicyEngineProtocol
    # ------------------------------------------------------------------

    def evaluate(self, event: ToolEvent) -> EngineDecision:
        """Run first-match-wins evaluation. Default: allow."""
        with self._lock:
            rules = list(self._rules)
        for rule in rules:
            if rule.matches(event):
                if rule.action == "deny":
                    return EngineDecision.block(
                        reason=rule.name,
                        message=rule.message or f"Blocked by rule {rule.name!r}",
                    )
                return EngineDecision.allow()
        return EngineDecision.allow()

    def __len__(self) -> int:
        with self._lock:
            return len(self._rules)
