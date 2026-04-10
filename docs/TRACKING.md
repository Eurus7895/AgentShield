# AgentShield — MVP Tracking Plan

> Updated: 2026-04-10
> Phase: MVP (Weeks 1–4)
> Status: Week 1 — COMPLETE. Week 2 — next.

---

## Current State

- [x] `engine/core.py` — ToolEvent, EngineDecision, AgentShieldEngine
- [x] `engine/policy.py` — YAML rule evaluation, first-match-wins, hot-reload
- [x] `engine/scanner.py` — 7 credential + 6 imperative language patterns
- [x] `engine/monitor.py` — Sliding-window loop detection, soft session caps
- [x] `daemon/server.py` — Asyncio Unix socket server, policy hot-reload
- [x] `daemon/startup.py` — launchd/systemd registration, PID fallback
- [x] `adapters/claude_code/pre_tool.py` — stdlib-only PreToolUse hook
- [x] `adapters/claude_code/post_tool.py` — stdlib-only PostToolUse hook
- [x] `adapters/claude_code/installer.py` — Idempotent settings.json merge
- [x] `policy/defaults.py` — 8 default rules (rm -rf, ssh, env, secrets, memory)
- [x] `storage/db.py` — SQLite WAL logger, dedup index, provenance columns
- [x] `storage/schema.sql` — tool_calls + sessions tables
- [x] `pyproject.toml` — package config with dependencies
- [x] `CLAUDE.md` — full project spec
- [x] 131 unit tests passing across 7 test files
- [x] Benchmark suite (`bench_hook.py`)
- [ ] `cli.py` — Week 2
- [ ] `dashboard/` — Week 2

---

## Week 1 — Daemon Core + Claude Code Adapter

**Goal:** `logs.db` has first real entry from a Claude Code hook in < 20ms.
**Status: COMPLETE**

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 1.1 | Policy engine (YAML rules, first-match-wins) | `engine/policy.py` | [x] 30 tests |
| 1.2 | SQLite audit logger (WAL, dedup index, framework col) | `storage/db.py`, `storage/schema.sql` | [x] 18 tests |
| 1.3 | Output scanner (credential/secret regex detection) | `engine/scanner.py` | [x] 21 tests |
| 1.4 | Session monitor (sliding-window loop detection) | `engine/monitor.py` | [x] 10 tests |
| 1.5 | Daemon server (Unix socket, receives ToolEvent, returns Decision) | `daemon/server.py` | [x] 10 tests |
| 1.6 | Daemon startup (launchd/systemd registration) | `daemon/startup.py` | [x] |
| 1.7 | `pre_tool.py` (stdlib only, connects to daemon via socket) | `adapters/claude_code/pre_tool.py` | [x] 15 tests |
| 1.8 | `post_tool.py` (stdlib only, credential scan results) | `adapters/claude_code/post_tool.py` | [x] |
| 1.9 | Default policy.yaml (block rm -rf, protect .ssh/.env) | `policy/defaults.py` | [x] 8 rules |
| 1.10 | Integration test: pre_tool → daemon → decision < 20ms | `tests/test_daemon.py` | [x] |
| 1.11 | Unit tests for all components | `tests/test_*.py` | [x] 131 tests |

**Week 1 exit criteria:**
- [x] `rm -rf /` → blocked in < 20ms
- [x] `cat README.md` → allowed in < 20ms
- [x] Daemon unreachable → fail-open (exit 0)
- [x] Every tool call logged to `logs.db` with correct schema

---

## Week 2 — CLI + Dashboard + Install Experience

**Goal:** `pip install agentshield && agentshield install` works in < 2 minutes.

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 2.1 | CLI: `agentshield install` (write hooks to settings.json, start daemon) | `adapters/claude_code/installer.py`, `cli.py` | [ ] |
| 2.2 | CLI: `agentshield status` (daemon health check) | `cli.py` | [ ] |
| 2.3 | CLI: `agentshield logs` (--last N, --blocked-only, --since) | `cli.py` | [ ] |
| 2.4 | CLI: `agentshield daemon start/stop/restart/status` | `cli.py` | [ ] |
| 2.5 | CLI: `agentshield policy check` (validate policy.yaml) | `cli.py` | [ ] |
| 2.6 | CLI: `agentshield export` (--format json/csv, --since) | `cli.py` | [ ] |
| 2.7 | Dashboard server (FastAPI, localhost:7432) | `dashboard/server.py` | [ ] |
| 2.8 | Dashboard UI (timeline view, blocked call highlights) | `dashboard/templates/index.html` | [ ] |
| 2.9 | CLI: `agentshield dashboard` (launch dashboard) | `cli.py` | [ ] |
| 2.10 | End-to-end test: install → hook fires → logs → dashboard shows | `tests/test_adapters.py` | [ ] |

**Week 2 exit criteria:**
- Fresh machine: `pip install agentshield && agentshield install` < 2 min
- `agentshield logs --blocked-only` shows blocked calls
- `agentshield dashboard` opens timeline at localhost:7432
- `agentshield status` reports daemon health

---

## Week 3 — PyPI + README + Launch

**Goal:** 50 installs. Package on PyPI, demo GIF, community posts.

| # | Task | Status |
|---|------|--------|
| 3.1 | Write README.md (problem, install, demo GIF, architecture diagram) | [ ] |
| 3.2 | Record demo GIF (install → block rm -rf → view dashboard) | [ ] |
| 3.3 | Publish to PyPI (`pip install agentshield`) | [ ] |
| 3.4 | Post: r/ClaudeAI | [ ] |
| 3.5 | Post: r/AIAgents | [ ] |
| 3.6 | Post: HN Show HN | [ ] |
| 3.7 | Post: Claude Code Discord | [ ] |
| 3.8 | Fix any install issues reported by early users | [ ] |

**Week 3 exit criteria:**
- `pip install agentshield` works from PyPI
- README has install instructions + demo GIF
- At least 3 community posts published
- Track install count

---

## Week 4 — User Validation

**Goal:** Talk to 5 users. Decide next direction based on feedback.

| # | Task | Status |
|---|------|--------|
| 4.1 | Reach out to 5+ installers for feedback calls | [ ] |
| 4.2 | Ask: "What made you install?" | [ ] |
| 4.3 | Ask: "What would you pay for?" | [ ] |
| 4.4 | Ask: "What's missing?" | [ ] |
| 4.5 | Document pain points and feature requests | [ ] |
| 4.6 | Decision gate (see below) | [ ] |

**Week 4 decision gate:**
- 3+ pain descriptions → build team features (Month 2)
- 1+ payment offer → build Stripe immediately
- MCP requests → build MCP adapter
- OpenSandbox user requests → build OpenSandbox integration

---

## Weekly Metrics to Track

| Metric | Week 1 | Week 2 | Week 3 | Week 4 |
|--------|--------|--------|--------|--------|
| PyPI installs | — | — | target: 50 | |
| GitHub stars | | | | |
| Bugs reported | 0 | | | |
| User conversations | — | — | — | target: 5 |
| Avg hook latency (ms) | < 20 | | | |
| Policy rules shipped | 8 | | | |
| Tests passing | 131 | | | |

---

## Build Order (dependency graph)

```
engine/core.py ✅
    │
    ├── engine/policy.py ✅ ───────┐
    ├── storage/db.py ✅ ──────────┤
    ├── engine/scanner.py ✅ ──────┤
    └── engine/monitor.py ✅ ──────┤
                                   ▼
                          daemon/server.py ✅
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
          adapters/claude_code/ ✅  daemon/startup.py ✅
          pre_tool.py + post_tool.py
                    │
                    ▼
               cli.py ← NEXT
                    │
                    ▼
            dashboard/server.py ← NEXT
```

Week 1 builds bottom-up: policy → storage → scanner → daemon → adapters. ✅ DONE
Week 2 builds top-down: CLI → dashboard.

---

## Risk Checklist (review weekly)

- [x] Hook latency stays < 20ms under real usage
- [x] `pre_tool.py` has zero non-stdlib imports
- [x] Daemon auto-restarts on crash (launchd/systemd)
- [x] SQLite WAL mode confirmed (no lock contention)
- [x] Fail-open works when daemon is down
- [x] No secrets in default policy.yaml
- [x] Install doesn't clobber existing settings.json hooks

---

*Next action: build `cli.py` (task 2.1)*
