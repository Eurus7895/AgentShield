# AgentShield

**Integrity and audit layer for AI agents.**

AgentShield intercepts every tool call an AI agent makes, enforces policy rules,
logs a complete audit trail, and detects credential leaks and anomalous behavior.
It is a slot-in component of the agent harness -- works alongside any orchestrator
(Claude Code, LangChain, CrewAI, OpenSandbox) without replacing what you already have.

```
OpenSandbox:   "Can this code run safely?"          -> Execution isolation
AgentShield:   "Should this agent be allowed to     -> Governance + compliance
                do this, and what did it do?"
```

---

## Status

**Week 1 complete. Week 2 (CLI + Dashboard) in progress.**

| Component | Status | Tests |
|-----------|--------|-------|
| Policy engine (YAML, first-match-wins, hot-reload) | Shipped | 30 |
| SQLite audit logger (WAL, dedup, provenance) | Shipped | 18 |
| Output scanner (7 credential + 6 imperative patterns) | Shipped | 21 |
| Session monitor (loop detection, soft caps) | Shipped | 10 |
| Daemon server (asyncio Unix socket, < 5ms IPC) | Shipped | 10 |
| Daemon startup (launchd / systemd --user) | Shipped | -- |
| Claude Code hooks (pre_tool.py + post_tool.py, stdlib only) | Shipped | 15 |
| Installer (idempotent settings.json merge) | Shipped | -- |
| Core engine (ToolEvent, EngineDecision) | Shipped | 27 |
| CLI (`agentshield install`, `logs`, `status`, `dashboard`) | Week 2 | -- |
| Dashboard (FastAPI, localhost:7432, timeline) | Week 2 | -- |

**131 tests passing.**

---

## How It Works

```
Agent invokes tool (e.g., bash "rm -rf /tmp")
    |
    v  Claude Code fires PreToolUse hook
pre_tool.py (stdlib only, ~2ms)
    |  sends ToolEvent via Unix socket
    v
AgentShield Daemon (long-running, full deps)
    |
    |-- Policy Engine   -> allow / block
    |-- SQLite Logger   -> what happened
    |-- Output Scanner  -> credential leak
    +-- Session Monitor -> loop detection
    |
    v  returns EngineDecision
pre_tool.py exits 0 (allow) or 2 (block)
```

Round-trip: **< 20ms** end-to-end.

---

## Default Policy

Out of the box, AgentShield blocks:

| Rule | What it protects |
|------|-----------------|
| `block_rm_rf` | Blocks `rm -rf` commands |
| `block_sudo_rm` | Blocks `sudo rm` commands |
| `block_format` | Blocks `mkfs`, `dd if=`, writes to `/dev/sd*` |
| `protect_ssh` | Denies read/write/edit to `.ssh/` |
| `protect_env` | Denies access to `.env`, `.env.local`, `.env.production` |
| `protect_secrets` | Denies access to `id_rsa`, `id_ed25519`, `*.pem`, `*.key` |
| `protect_memory` | Write-protects `MEMORY.md`, `memory/`, `.claude/memory/` |

Custom rules via `~/.agentshield/policy.yaml`:

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

---

## OWASP Agentic AI Coverage

| # | Risk | AgentShield Response | Status |
|---|------|---------------------|--------|
| 1 | Goal Hijack | Imperative-language detection in output scanner | Shipped (partial) |
| 2 | Tool Misuse | Policy engine + PreToolUse blocking | Shipped |
| 3 | Identity Abuse | Per-agent-role policies via agent_id/agent_type | Shipped (partial) |
| 4 | Delegated Authority | Provenance ledger with source linking | Shipped (partial) |
| 5 | Insecure Output | PostToolUse credential scanning (7 patterns) | Shipped |
| 6 | Memory Poisoning | Memory guardian -- write-protect MEMORY.md | Shipped |
| 7 | Multi-Agent Cascade | Session isolation + cross-agent audit | Planned |
| 8 | Infinite Loop | Sliding-window loop detection | Shipped |
| 9 | False Completion | Session replay timeline | Week 2 |
| 10 | Semantic Bypass | LLM-powered intent analysis | Planned |

---

## Architecture

```
agentshield/
    engine/
        core.py         AgentShieldEngine, ToolEvent, EngineDecision
        policy.py       YAML rule evaluation, first-match-wins
        scanner.py      Credential/PII + imperative language detection
        monitor.py      Session monitor, loop detection
    daemon/
        server.py       Asyncio Unix socket server
        startup.py      launchd/systemd service registration
    adapters/
        claude_code/
            pre_tool.py     PreToolUse hook (stdlib only)
            post_tool.py    PostToolUse hook (stdlib only)
            installer.py    Settings.json merge + daemon startup
    policy/
        defaults.py     8 default security rules
    storage/
        db.py           SQLite WAL audit logger
        schema.sql      tool_calls + sessions tables
```

---

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Daemon | Python 3.10+ | Long-running, full deps |
| Hook scripts | stdlib only | Fast spawn, no pip overhead |
| IPC | Unix socket | ~2-5ms, local, no network |
| Policy | PyYAML | Human-readable, hot-reload |
| Storage | SQLite WAL | Local, fast, concurrent reads |
| CLI | Typer | Clean, type-safe (Week 2) |
| Dashboard | FastAPI + plain HTML | No build step (Week 2) |
| Daemon mgmt | launchd / systemd --user | Native, auto-restart |

---

## License

MIT
