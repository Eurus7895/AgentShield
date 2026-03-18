# AgentShield — MVP Tracking Plan

> Updated: 2026-03-18
> Phase: MVP (Weeks 1–4)
> Status: Week 1 — in progress

---

## Current State

- [x] `engine/core.py` — ToolEvent, EngineDecision, AgentShieldEngine
- [x] `pyproject.toml` — package config with dependencies
- [x] `CLAUDE.md` — full project spec
- [ ] Everything else

---

## Week 1 — Daemon Core + Claude Code Adapter

**Goal:** `logs.db` has first real entry from a Claude Code hook in < 20ms.

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 1.1 | Policy engine (YAML rules, first-match-wins) | `engine/policy.py` | [ ] |
| 1.2 | SQLite audit logger (WAL, dedup index, framework col) | `storage/db.py`, `storage/schema.sql` | [ ] |
| 1.3 | Output scanner (credential/secret regex detection) | `engine/scanner.py` | [ ] |
| 1.4 | Session monitor stub (loop detection placeholder) | `engine/monitor.py` | [ ] |
| 1.5 | Daemon server (Unix socket, receives ToolEvent, returns Decision) | `daemon/server.py` | [ ] |
| 1.6 | Daemon startup (launchd/systemd registration) | `daemon/startup.py` | [ ] |
| 1.7 | `pre_tool.py` (stdlib only, connects to daemon via socket) | `adapters/claude_code/pre_tool.py` | [ ] |
| 1.8 | `post_tool.py` (stdlib only, credential scan results) | `adapters/claude_code/post_tool.py` | [ ] |
| 1.9 | Default policy.yaml (block rm -rf, protect .ssh/.env) | `policy/defaults.py` | [ ] |
| 1.10 | Integration test: pre_tool → daemon → decision < 20ms | `tests/test_daemon.py` | [ ] |
| 1.11 | Unit tests for policy engine | `tests/test_policy.py` | [ ] |

**Week 1 exit criteria:**
- `rm -rf /` → blocked in < 20ms
- `cat README.md` → allowed in < 20ms
- Daemon unreachable → fail-open (exit 0)
- Every tool call logged to `logs.db` with correct schema

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
| Bugs reported | | | | |
| User conversations | — | — | — | target: 5 |
| Avg hook latency (ms) | target: <20 | | | |
| Policy rules shipped | | | | |

---

## Build Order (dependency graph)

```
engine/core.py ✅
    │
    ├── engine/policy.py ──────────┐
    ├── storage/db.py ─────────────┤
    ├── engine/scanner.py ─────────┤
    └── engine/monitor.py ─────────┤
                                   ▼
                          daemon/server.py
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
          adapters/claude_code/    daemon/startup.py
          pre_tool.py + post_tool.py
                    │
                    ▼
               cli.py + installer.py
                    │
                    ▼
            dashboard/server.py
```

Week 1 builds bottom-up: policy → storage → scanner → daemon → adapters.
Week 2 builds top-down: CLI → installer → dashboard.

---

## Risk Checklist (review weekly)

- [ ] Hook latency stays < 20ms under real usage
- [ ] `pre_tool.py` has zero non-stdlib imports
- [ ] Daemon auto-restarts on crash (launchd/systemd)
- [ ] SQLite WAL mode confirmed (no lock contention)
- [ ] Fail-open works when daemon is down
- [ ] No secrets in default policy.yaml
- [ ] Install doesn't clobber existing settings.json hooks

---

*Next action: build `engine/policy.py` (task 1.1)*
