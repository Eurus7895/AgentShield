"""Default policy.yaml for AgentShield Week 1 MVP.

Rules grouped by the responsibility they serve:
  R1 Allow/deny enforcement: block_rm_rf, block_sudo_rm, block_format
  R3 Memory guardian:        protect_memory, protect_autodream_output,
                             protect_claude_memory
  R1 Credential protection:  protect_ssh, protect_env, protect_secrets
  R4 Role arbiter (example): evaluator_readonly

Priorities are chosen so more specific rules win over broader ones.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_POLICY_YAML = """\
version: 1

# AgentShield default policy — edit to taste.
# Rules are evaluated in priority order (desc). First match wins.
# Default action when no rule matches: allow.

rules:

  # --------------------------------------------------------------------
  # R1 — Dangerous bash commands
  # --------------------------------------------------------------------

  - name: block_rm_rf
    tool: bash
    match: "rm -rf"
    action: deny
    priority: 100
    message: "Dangerous recursive deletion blocked by AgentShield"

  - name: block_sudo_rm
    tool: bash
    match: "sudo rm"
    action: deny
    priority: 100
    message: "sudo rm blocked by AgentShield"

  - name: block_format
    tool: bash
    match:
      - "mkfs"
      - "dd if="
      - "> /dev/sd"
    action: deny
    priority: 100
    message: "Disk-format command blocked by AgentShield"

  # --------------------------------------------------------------------
  # R1 — Credential protection (SSH, env, private keys)
  # --------------------------------------------------------------------

  - name: protect_ssh
    tool:
      - read
      - write
      - edit
    path_match:
      - ".ssh/"
      - "id_rsa"
      - "id_ed25519"
    action: deny
    priority: 90
    message: "SSH directory and keys are protected by AgentShield"

  - name: protect_env
    tool:
      - read
      - write
      - edit
    path_match:
      - ".env"
      - ".env.local"
      - ".env.production"
    action: deny
    priority: 90
    message: ".env files are protected by AgentShield"

  - name: protect_secrets
    tool:
      - read
      - write
      - edit
    path_match:
      - "*.pem"
      - "*.key"
      - "credentials.json"
    action: deny
    priority: 90
    message: "Secret files are protected by AgentShield"

  # --------------------------------------------------------------------
  # R3 — Memory guardian (harness integrity)
  # --------------------------------------------------------------------

  - name: protect_memory
    tool:
      - write
      - edit
    path_match:
      - "MEMORY.md"
      - "memory/*.md"
      - "memory/*"
    action: deny
    priority: 95
    message: "Agent memory files are write-protected by AgentShield"

  - name: protect_claude_memory
    tool:
      - write
      - edit
    path_match:
      - ".claude/memory/*"
      - ".claude/CLAUDE.md"
    action: deny
    priority: 95
    message: "Claude Code harness memory is write-protected by AgentShield"

  - name: protect_autodream_output
    tool:
      - write
      - edit
    path_match:
      - ".autodream/*"
      - "autodream/*"
    action: deny
    priority: 95
    message: "autoDream outputs are write-protected by AgentShield"

  # --------------------------------------------------------------------
  # R4 — Per-role example (demonstrates agent_id matching)
  # --------------------------------------------------------------------

  - name: evaluator_readonly
    tool:
      - write
      - edit
      - bash
    agent_id:
      - "evaluator-*"
      - "evaluator"
    action: deny
    priority: 80
    message: "Evaluator agents are read-only by AgentShield policy"
"""


def default_rules() -> list[dict]:
    """Return the default rule list as parsed dicts (for testing)."""
    import yaml

    data = yaml.safe_load(DEFAULT_POLICY_YAML) or {}
    return list(data.get("rules", []))


def write_default_policy(path: str | Path) -> Path:
    """Write the default policy YAML to path. Returns the resolved path."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(DEFAULT_POLICY_YAML, encoding="utf-8")
    return p
