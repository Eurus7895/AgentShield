# AgentShield — Tech Stack

> Last updated: 2026-03-18

---

## Overview

AgentShield's tech stack is chosen for **speed, simplicity, and zero external
infrastructure**. Everything runs locally — no cloud services, no Docker, no
database servers. This keeps the tool lightweight for individual developers
while leaving a clean upgrade path for team and enterprise features.

---

## Stack Summary

| Layer | Technology | Version | Why |
|-------|-----------|---------|-----|
| **Language** | Python | 3.10+ | Matches the AI/ML ecosystem; most agent frameworks are Python |
| **Hook Scripts** | Python (stdlib only) | 3.10+ | No pip deps — fast spawn, no import overhead |
| **IPC** | Unix domain socket | — | ~2-5ms latency; local, no network stack |
| **Policy Format** | YAML (via PyYAML) | — | Human-readable, easy to edit, widely understood |
| **Storage** | SQLite | WAL mode | Local, fast, concurrent reads, zero setup |
| **CLI Framework** | Typer | — | Type-safe, auto-generated help, clean UX |
| **Dashboard** | FastAPI + plain HTML | — | No React build step; serves from a single process |
| **Daemon Mgmt** | launchd / systemd --user | — | Native OS process management; auto-restart |
| **Packaging** | pyproject.toml | — | Modern Python packaging standard (PEP 621) |
| **Testing** | pytest | — | Standard Python test runner |

---

## Detailed Choices

### Python 3.10+

- **Why 3.10:** `match` statement support, union type syntax (`str | None`),
  stable dataclasses.
- **Why Python:** The AI agent ecosystem (LangChain, CrewAI, Claude Code hooks)
  is overwhelmingly Python. Using the same language reduces friction for
  contributors and integration.

### stdlib-Only Hook Scripts

The Claude Code hook scripts (`pre_tool.py`, `post_tool.py`) are invoked on
**every tool call**. They must:

- Start fast (no pip dependency loading)
- Use only Python standard library modules
- Communicate with the daemon via Unix socket (`socket`, `json` modules)
- Exit with the correct code (0 = allow, 2 = block, 1 = error)

This constraint drives the daemon architecture — all heavy logic lives in the
daemon, not the hook scripts.

### Unix Domain Socket (IPC)

```
pre_tool.py  ──[ToolEvent JSON]──▶  agentshield.sock  ──▶  Daemon
pre_tool.py  ◀──[Decision JSON]──  agentshield.sock  ◀──  Daemon
```

- **Latency:** ~2-5ms round trip (vs ~80-150ms for spawning a new Python process
  with dependencies).
- **Location:** `~/.agentshield/agentshield.sock`
- **Protocol:** Line-delimited JSON over stream socket.
- **Platform note:** Unix sockets work on macOS and Linux. Windows/WSL support
  is a future consideration (named pipes or TCP localhost fallback).

### PyYAML (Policy Engine)

- YAML is the de facto format for security rules and configuration.
- Human-readable and diffable — important for version-controlled policy files.
- Hot-reload: daemon watches `policy.yaml` with a debounced 500ms reload.

### SQLite (WAL Mode)

- **Why SQLite:** Zero-config, file-based, ships with Python. No database server
  to install or manage.
- **Why WAL mode:** Allows concurrent reads (dashboard) while writes (hooks)
  continue without blocking. Essential for a tool that logs on every tool call.
- **Deduplication:** A `UNIQUE INDEX` on `(session_id, tool, ts, framework)`
  prevents double-logging when multiple adapters are active.
- **Location:** `~/.agentshield/logs.db`

### Typer (CLI)

- Type-safe argument parsing via Python type hints.
- Auto-generated `--help` output.
- Clean subcommand structure: `agentshield install`, `agentshield logs`,
  `agentshield daemon start`, etc.

### FastAPI + Plain HTML (Dashboard)

- **Why FastAPI:** Async-capable, fast, minimal boilerplate. Serves both the API
  and static HTML templates.
- **Why plain HTML:** No React, no Webpack, no build step. The dashboard is a
  single-page timeline view — CSS and vanilla JS are sufficient.
- **Default port:** `localhost:7432`

### launchd / systemd --user (Daemon Management)

- **macOS:** `launchd` plist in `~/Library/LaunchAgents/`
- **Linux:** `systemd --user` unit file
- Both provide: auto-start on login, auto-restart on crash, log capture.
- Fallback: manual `agentshield daemon start` for environments without init systems.

### pyproject.toml (Packaging)

- PEP 621 compliant.
- Single source of truth for project metadata, dependencies, and build configuration.
- Enables `pip install agentshield` from PyPI.

---

## Dependency Tree

### Runtime Dependencies

| Package | Purpose |
|---------|---------|
| `pyyaml` | Policy file parsing |
| `typer` | CLI framework |
| `fastapi` | Dashboard server |
| `uvicorn` | ASGI server for FastAPI |

### Development Dependencies

| Package | Purpose |
|---------|---------|
| `pytest` | Test runner |
| `ruff` | Linter and formatter |

### Hook Scripts (Zero Dependencies)

`pre_tool.py` and `post_tool.py` use **only** these stdlib modules:

- `sys` — stdin reading, exit codes
- `json` — event serialization
- `socket` — Unix socket IPC
- `os` — path operations
- `datetime` — timestamps

---

## Performance Targets

| Metric | Target | Measured by |
|--------|--------|------------|
| Hook → daemon → decision | < 20ms | `tests/test_daemon.py` |
| Policy evaluation | < 1ms | `tests/test_policy.py` |
| SQLite insert | < 5ms | `tests/test_engine.py` |
| Dashboard page load | < 500ms | Manual testing |
| `pip install agentshield` | < 30s | Clean machine test |
| `agentshield install` | < 10s | Writes config + starts daemon |

---

## Platform Support

| Platform | Status | Notes |
|----------|--------|-------|
| macOS (Apple Silicon) | Primary | launchd daemon management |
| macOS (Intel) | Supported | Same as above |
| Linux (x86_64) | Supported | systemd --user daemon management |
| Linux (ARM64) | Supported | Same as above |
| Windows (WSL) | Future | Unix sockets work in WSL; native Windows needs named pipes |
| Windows (native) | Not planned | Would require TCP localhost fallback |
