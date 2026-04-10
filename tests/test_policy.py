"""Tests for agentshield.engine.policy and agentshield.policy.defaults."""

from __future__ import annotations

import pytest

from agentshield.engine.core import PolicyEngineProtocol, ToolEvent
from agentshield.engine.policy import (
    PolicyEngine,
    PolicyRule,
    compile_rule,
    compile_rules,
)
from agentshield.policy.defaults import DEFAULT_POLICY_YAML, default_rules


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


# ---------------------------------------------------------------------------
# Rule compilation
# ---------------------------------------------------------------------------


class TestCompile:
    def test_scalar_fields_become_tuples(self):
        rule = compile_rule(
            {"name": "r1", "tool": "bash", "match": "rm -rf", "action": "deny"}
        )
        assert rule.tool == ("bash",)
        assert rule.match == ("rm -rf",)

    def test_list_fields_become_tuples(self):
        rule = compile_rule(
            {
                "name": "r2",
                "tool": ["read", "write"],
                "path_match": [".env", ".env.local"],
                "action": "deny",
            }
        )
        assert rule.tool == ("read", "write")
        assert rule.path_match == (".env", ".env.local")

    def test_invalid_action_raises(self):
        with pytest.raises(ValueError):
            compile_rule({"name": "bad", "action": "maybe"})

    def test_compile_rules_sorts_by_priority_desc(self):
        rules = compile_rules(
            [
                {"name": "low", "action": "deny", "priority": 1},
                {"name": "high", "action": "deny", "priority": 10},
                {"name": "mid", "action": "deny", "priority": 5},
            ]
        )
        assert [r.name for r in rules] == ["high", "mid", "low"]


# ---------------------------------------------------------------------------
# Matching semantics
# ---------------------------------------------------------------------------


class TestMatching:
    def test_tool_wildcard_matches(self):
        rule = compile_rule({"name": "any", "tool": "*", "action": "deny"})
        assert rule.matches(make_event(tool_name="bash"))
        assert rule.matches(make_event(tool_name="read"))

    def test_tool_exact_match(self):
        rule = compile_rule({"name": "bash_only", "tool": "bash", "action": "deny"})
        assert rule.matches(make_event(tool_name="bash"))
        assert not rule.matches(make_event(tool_name="read"))

    def test_substring_match(self):
        rule = compile_rule(
            {"name": "rm", "tool": "bash", "match": "rm -rf", "action": "deny"}
        )
        assert rule.matches(
            make_event(tool_name="bash", tool_input={"command": "rm -rf /"})
        )
        assert not rule.matches(
            make_event(tool_name="bash", tool_input={"command": "ls"})
        )

    def test_path_match_substring(self):
        rule = compile_rule(
            {
                "name": "ssh",
                "tool": "read",
                "path_match": ".ssh/",
                "action": "deny",
            }
        )
        assert rule.matches(
            make_event(
                tool_name="read", tool_input={"file_path": "/home/u/.ssh/id_rsa"}
            )
        )

    def test_path_match_glob_on_basename(self):
        rule = compile_rule(
            {
                "name": "pem",
                "tool": "read",
                "path_match": "*.pem",
                "action": "deny",
            }
        )
        assert rule.matches(
            make_event(tool_name="read", tool_input={"file_path": "/tmp/cert.pem"})
        )
        assert not rule.matches(
            make_event(tool_name="read", tool_input={"file_path": "/tmp/cert.txt"})
        )

    def test_agent_id_wildcard(self):
        rule = compile_rule(
            {
                "name": "eval_lock",
                "tool": "write",
                "agent_id": "evaluator-*",
                "action": "deny",
            }
        )
        assert rule.matches(
            make_event(tool_name="write", agent_id="evaluator-1")
        )
        assert not rule.matches(
            make_event(tool_name="write", agent_id="generator-1")
        )

    def test_agent_type_match(self):
        rule = compile_rule(
            {
                "name": "sub_lock",
                "tool": "bash",
                "agent_type": "subagent",
                "action": "deny",
            }
        )
        assert rule.matches(make_event(tool_name="bash", agent_type="subagent"))
        assert not rule.matches(make_event(tool_name="bash", agent_type="main"))


# ---------------------------------------------------------------------------
# PolicyEngine.evaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    def test_default_allow_when_no_rules(self):
        engine = PolicyEngine(rules=[])
        decision = engine.evaluate(make_event())
        assert decision.action == "allow"

    def test_first_match_wins_by_priority(self):
        # Low priority allow; high priority deny → deny wins
        engine = PolicyEngine(
            rules=compile_rules(
                [
                    {"name": "permit", "tool": "bash", "action": "allow", "priority": 1},
                    {
                        "name": "deny_rm",
                        "tool": "bash",
                        "match": "rm",
                        "action": "deny",
                        "priority": 10,
                    },
                ]
            )
        )
        decision = engine.evaluate(
            make_event(tool_name="bash", tool_input={"command": "rm -rf /"})
        )
        assert decision.is_blocked
        assert decision.reason == "deny_rm"

    def test_allow_rule_short_circuits_deny(self):
        # Allow rule with higher priority should beat a later deny.
        engine = PolicyEngine(
            rules=compile_rules(
                [
                    {
                        "name": "workspace_ok",
                        "tool": "*",
                        "path_match": "/workspace/",
                        "action": "allow",
                        "priority": 100,
                    },
                    {
                        "name": "no_writes",
                        "tool": "write",
                        "action": "deny",
                        "priority": 10,
                    },
                ]
            )
        )
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                tool_input={"file_path": "/workspace/project/foo.py"},
            )
        )
        assert decision.action == "allow"

    def test_block_carries_rule_name_and_message(self):
        engine = PolicyEngine(
            rules=compile_rules(
                [
                    {
                        "name": "block_rm_rf",
                        "tool": "bash",
                        "match": "rm -rf",
                        "action": "deny",
                        "message": "nope",
                    }
                ]
            )
        )
        decision = engine.evaluate(
            make_event(tool_input={"command": "rm -rf /tmp/x"})
        )
        assert decision.reason == "block_rm_rf"
        assert decision.message == "nope"


# ---------------------------------------------------------------------------
# from_yaml / from_path / reload_from_path
# ---------------------------------------------------------------------------


class TestYamlLoading:
    def test_from_yaml_parses_rules(self):
        yaml_text = """\
version: 1
rules:
  - name: block_rm_rf
    tool: bash
    match: "rm -rf"
    action: deny
"""
        engine = PolicyEngine.from_yaml(yaml_text)
        assert len(engine) == 1
        assert engine.evaluate(
            make_event(tool_input={"command": "rm -rf /"})
        ).is_blocked

    def test_from_path_and_reload(self, tmp_path):
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            """\
version: 1
rules:
  - name: block_ls
    tool: bash
    match: "ls"
    action: deny
"""
        )
        engine = PolicyEngine.from_path(policy_file)
        assert engine.evaluate(
            make_event(tool_input={"command": "ls"})
        ).is_blocked

        # Rewrite with a different rule; reload must swap atomically.
        policy_file.write_text(
            """\
version: 1
rules:
  - name: block_pwd
    tool: bash
    match: "pwd"
    action: deny
"""
        )
        engine.reload_from_path(policy_file)
        assert not engine.evaluate(
            make_event(tool_input={"command": "ls"})
        ).is_blocked
        assert engine.evaluate(
            make_event(tool_input={"command": "pwd"})
        ).is_blocked


# ---------------------------------------------------------------------------
# Default policy covers harness responsibilities
# ---------------------------------------------------------------------------


class TestDefaults:
    @pytest.fixture
    def engine(self) -> PolicyEngine:
        return PolicyEngine.from_yaml(DEFAULT_POLICY_YAML)

    def test_protocol_conformance(self, engine):
        assert isinstance(engine, PolicyEngineProtocol)

    def test_default_rules_nonempty(self):
        rules = default_rules()
        assert len(rules) >= 8
        names = {r["name"] for r in rules}
        expected = {
            "block_rm_rf",
            "block_sudo_rm",
            "block_format",
            "protect_ssh",
            "protect_env",
            "protect_secrets",
            "protect_memory",
            "protect_claude_memory",
            "protect_autodream_output",
            "evaluator_readonly",
        }
        assert expected.issubset(names)

    # R1 — Dangerous bash

    def test_rm_rf_blocked(self, engine):
        decision = engine.evaluate(
            make_event(tool_input={"command": "rm -rf /tmp/danger"})
        )
        assert decision.is_blocked
        assert decision.reason == "block_rm_rf"

    def test_sudo_rm_blocked(self, engine):
        decision = engine.evaluate(
            make_event(tool_input={"command": "sudo rm -f /etc/passwd"})
        )
        assert decision.is_blocked

    def test_mkfs_blocked(self, engine):
        decision = engine.evaluate(
            make_event(tool_input={"command": "mkfs.ext4 /dev/sda1"})
        )
        assert decision.is_blocked

    def test_normal_bash_allowed(self, engine):
        for cmd in ("ls -la", "pwd", "cat README.md", "git status"):
            decision = engine.evaluate(
                make_event(tool_input={"command": cmd})
            )
            assert not decision.is_blocked, f"{cmd!r} should be allowed"

    # R1 — Credential protection

    def test_ssh_read_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="read", tool_input={"file_path": "/home/u/.ssh/id_rsa"}
            )
        )
        assert decision.is_blocked

    def test_env_write_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                tool_input={"file_path": "/workspace/.env", "content": "SECRET=x"},
            )
        )
        assert decision.is_blocked

    def test_pem_read_blocked(self, engine):
        decision = engine.evaluate(
            make_event(tool_name="read", tool_input={"file_path": "/tmp/key.pem"})
        )
        assert decision.is_blocked

    # R3 — Memory guardian

    def test_memory_write_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                tool_input={"file_path": "MEMORY.md", "content": "poison"},
            )
        )
        assert decision.is_blocked
        assert decision.reason == "protect_memory"

    def test_memory_subdir_write_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                tool_input={"file_path": "memory/notes.md", "content": "x"},
            )
        )
        assert decision.is_blocked

    def test_claude_memory_write_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                tool_input={"file_path": ".claude/memory/ideas.md", "content": "x"},
            )
        )
        assert decision.is_blocked

    def test_autodream_write_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                tool_input={"file_path": ".autodream/summary.md", "content": "x"},
            )
        )
        assert decision.is_blocked

    def test_memory_read_allowed(self, engine):
        # Memory should be readable — only writes are blocked.
        decision = engine.evaluate(
            make_event(tool_name="read", tool_input={"file_path": "MEMORY.md"})
        )
        assert not decision.is_blocked

    # R4 — Per-role

    def test_evaluator_write_blocked(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                agent_id="evaluator-1",
                tool_input={"file_path": "/tmp/ok.txt", "content": "x"},
            )
        )
        assert decision.is_blocked
        assert decision.reason == "evaluator_readonly"

    def test_generator_write_allowed(self, engine):
        decision = engine.evaluate(
            make_event(
                tool_name="write",
                agent_id="generator-1",
                tool_input={"file_path": "/tmp/ok.txt", "content": "x"},
            )
        )
        assert not decision.is_blocked
