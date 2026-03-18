# AgentShield — Architecture

> Last updated: 2026-03-18

---

## Overview

AgentShield uses a **Daemon + Adapter** pattern to intercept agent tool calls,
enforce policy, and log an audit trail — all with minimal latency impact on the
developer workflow.

```
Agent (Claude Code, MCP client, SDK, OpenSandbox)
    │
    ▼  via Adapter (Hook / MCP / SDK)
┌──────────────────────────────────────┐
│         AgentShield Engine           │
│                                      │
│  Policy Engine   → allow / block     │
│  Audit Logger    → what happened     │
│  Output Scanner  → credential leak   │
│  Session Monitor → loop detection    │
└──────────────────────────────────────┘
```

---

## Core Design: Daemon + Adapter

### Why a Daemon?

The Claude Code hook scripts (`pre_tool.py`, `post_tool.py`) must be **stdlib-only**
to avoid pip dependency overhead on every tool call invocation. However, policy
evaluation requires PyYAML and full engine logic.

**Solution:** A long-running daemon process holds all dependencies and serves
decisions over a Unix socket.

```
pre_tool.py (stdlib only, ~2-5ms)
    │ sends ToolEvent via Unix socket
    ▼
AgentShield Daemon (long-running, full deps)
    │
    ├── Policy Engine  (PyYAML rules, first-match-wins)
    ├── SQLite Logger  (WAL mode, dedup index)
    ├── Output Scanner (credential/PII regex)
    └── Session Monitor (loop detection)
    │
    ▼ returns EngineDecision
pre_tool.py exits 0 (allow) or 2 (block)
```

### Performance Comparison

| Approach | Latency per call |
|----------|-----------------|
| Spawn Python + load deps per call | ~80–150ms |
| Daemon + Unix socket IPC | ~2–5ms |

Developers notice latency above ~100ms. The daemon approach keeps tool calls
feeling instant.

### Daemon Lifecycle

- **Auto-start:** `agentshield install` registers the daemon with `launchd` (macOS) or `systemd --user` (Linux).
- **Auto-restart:** Managed by the OS init system on crash.
- **Health check:** `agentshield status` reports daemon state.
- **Socket location:** `~/.agentshield/agentshield.sock`

---

## Adapter Layer

Adapters translate framework-specific events into AgentShield's `ToolEvent` format.

### Adapter 1 — Claude Code Hook (MVP)

Uses the official Claude Code Hooks API (`PreToolUse` + `PostToolUse`).

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "",
      "hooks": [{ "type": "command", "command": "python3 ~/.agentshield/pre_tool.py" }]
    }],
    "PostToolUse": [{
      "matcher": "",
      "hooks": [{ "type": "command", "command": "python3 ~/.agentshield/post_tool.py" }]
    }]
  }
}
```

**Hook protocol:**
- **stdin:** JSON with `tool_name`, `tool_input`, `session_id`, `agent_id`, `agent_type`
- **exit 0** → allow
- **exit 2** → block
- **exit 1** → hook error

### Adapter 2 — MCP Server (Post-MVP)

Any MCP-compatible agent routes tool calls through AgentShield's MCP Server for
policy enforcement and audit logging.

### Adapter 3 — Python SDK (Month 2+)

`@shield.protect()` decorator for LangChain, CrewAI, and custom agent frameworks.

### Adapter 4 — OpenSandbox Integration (Month 3+)

Governance layer wrapping OpenSandbox execution. Agents run in OpenSandbox
(isolation) while monitored by AgentShield (governance).

---

## Core Engine Interface

```python
@dataclass
class ToolEvent:
    tool_name: str      # "bash", "read", "write", etc.
    tool_input: dict    # tool arguments
    session_id: str     # agent session ID
    agent_id: str       # which agent (subagent support)
    agent_type: str     # "main" | "subagent"
    framework: str      # "claude_code" | "mcp" | "sdk" | "opensandbox"
    timestamp: str      # ISO format

@dataclass
class EngineDecision:
    action: str         # "allow" or "block"
    reason: str | None  # rule name if blocked
    message: str | None # shown to agent if blocked
```

---

## Database Schema

SQLite in WAL mode for concurrent reads during hook writes.

```sql
CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    session_id  TEXT,
    agent_id    TEXT,
    framework   TEXT NOT NULL DEFAULT 'claude_code',
    tool        TEXT NOT NULL,
    input       TEXT,
    blocked     INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT,
    framework   TEXT NOT NULL DEFAULT 'claude_code',
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    tool_count  INTEGER NOT NULL DEFAULT 0,
    block_count INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup
    ON tool_calls(session_id, tool, ts, framework);
```

The `framework` column tracks which adapter generated each event — the foundation
for cross-framework governance analytics.

---

## Policy Engine

YAML-based rules with **first-match-wins** evaluation (sorted by priority descending).
Default action is **allow** when no rule matches.

```yaml
version: 1
rules:
  - name: block_rm_rf
    tool: bash
    match: "rm -rf"
    action: deny
    message: "Dangerous deletion blocked by AgentShield"

  - name: protect_ssh
    tool: [read, write, edit]
    path_match: ".ssh/"
    action: deny
    message: "SSH directory protected"
```

Policy hot-reload: the daemon watches `policy.yaml` and reloads on change
(debounced 500ms).

---

## Fail Behavior

**MVP default: fail-open** — if the daemon is unreachable, `pre_tool.py` exits 0
(allow) and logs the error to `~/.agentshield/errors.log`.

Rationale: developer tool first — don't break the workflow.

**Future:** `fail_behavior: open|closed` in `policy.yaml`. Enterprise/production
workspaces should default to fail-closed.

---

## File Structure

```
agentshield/
    __init__.py
    daemon/
        server.py               ← Unix socket daemon
        startup.py              ← launchd/systemd registration
    engine/
        core.py                 ← ToolEvent, EngineDecision, AgentShieldEngine
        policy.py               ← YAML rule evaluation
        logger.py               ← SQLite WAL logging
        scanner.py              ← Credential/PII detection
        monitor.py              ← Session monitor, loop detection
    adapters/
        claude_code/
            pre_tool.py         ← PreToolUse hook (stdlib only)
            post_tool.py        ← PostToolUse hook (stdlib only)
            installer.py        ← Writes settings.json, starts daemon
        mcp_server/             ← Post-MVP
        sdk/                    ← Post-MVP
        opensandbox/            ← Month 3+
    policy/
        loader.py
        defaults.py
    storage/
        db.py
        schema.sql
    dashboard/
        server.py
        templates/
            index.html
    cli.py

~/.agentshield/
    pre_tool.py
    post_tool.py
    policy.yaml
    logs.db
    errors.log
    agentshield.sock
```

---

## Diagram: Request Flow

```
1. Agent invokes tool (e.g., bash "rm -rf /tmp")
2. Claude Code fires PreToolUse hook
3. pre_tool.py reads stdin JSON, sends ToolEvent to daemon via Unix socket
4. Daemon evaluates policy rules (first-match-wins)
5. Daemon logs event to SQLite
6. Daemon returns EngineDecision to pre_tool.py
7. pre_tool.py exits 0 (allow) or 2 (block)
8. If PostToolUse: post_tool.py scans output for credentials, logs result
```

Round-trip target: **< 20ms** end-to-end.
