# CLAUDE.md — AgentShield

> Context anchor for every coding session. Read this file before doing anything.
> This file contains the complete project context — strategy, architecture, roadmap, decisions.

---

## One Sentence

AgentShield intercepts every AI agent tool call, enforces policy, and logs everything —
starting as a security observer for Claude Code, evolving into the runtime every agent team must use.

---

## The Problem

Every AI agent running today has the same critical gap:

```
What agents CAN do:     read files, delete files, run bash, call APIs,
                        leak secrets, modify configs, push code

What teams KNOW about:  almost nothing — unless something breaks

What teams CAN prevent: almost nothing — without hardcoding per-agent
```

Validated by research:
- "Agents of Chaos" (Harvard/Stanford/MIT, Feb 2026): agents destroyed mail servers,
  looped 9 days burning 60,000 tokens, leaked PII by changing one word
- OWASP Top 10 for Agentic AI (Dec 2025): industry's first codified agent security framework
- 48% of security professionals rank agentic AI as #1 attack vector in 2026
- Only 34% of enterprises have AI-specific security controls

This is exactly where AWS was before IAM existed.

---

## What AgentShield Is — Honest Definition

**Current MVP: Security Observer**
AgentShield sits alongside the agent, intercepts tool calls via hooks,
enforces policy, logs everything. Agent runs normally — AgentShield watches and acts.

```
Agent → [AgentShield Observer] → Tool Execution
              ↑
         log + block
```

**This is NOT a runtime yet.** An agent can bypass AgentShield if it doesn't
go through the hook path. Known limitation. Accepted tradeoff for fast MVP.

**Long-term vision: Security Runtime**
Agent runs INSIDE AgentShield's execution environment. No bypass possible.
Build time: Month 9-12.

**Path:** Observer (MVP) → Controller (v2) → Runtime (v3) → Infrastructure Standard

---

## Why Now

```
Dec 2025:  OWASP Top 10 for Agentic AI published
Jan 2026:  OpenClaw — 512 vulnerabilities, 135,000 exposed instances
Feb 2026:  "Agents of Chaos" paper published
Mar 2026:  AgentShield — start building
6-12mo:    Anthropic/OpenAI ship native controls → window narrows
```

---

## OWASP Coverage

| # | Risk | AgentShield Response | Phase |
|---|------|---------------------|-------|
| 1 | Goal Hijack | Prompt injection scanner (static v1, AI v2) | Layer 2 |
| 2 | Tool Misuse | Policy engine + PreToolUse blocking | **MVP** |
| 3 | Identity Abuse | Agent identity + command allowlist | Layer 3 |
| 4 | Delegated Authority | Agent chain audit + visualization | Layer 2 |
| 5 | Insecure Output | PostToolUse credential scanning | **MVP** |
| 6 | Memory Poisoning | Memory vault + isolated memory per agent | Layer 3 |
| 7 | Multi-Agent Cascade | Session isolation + cross-agent audit | Layer 2 |
| 8 | Infinite Loop | Circuit breaker + session monitor | **MVP** |
| 9 | False Completion | Session replay timeline | **MVP** |
| 10 | Semantic Bypass | LLM-powered intent analysis | Layer 3 |

MVP covers 4/10 fully. Layer 2 adds 3. Layer 3 completes the set.

---

## Architecture

### Core Design: Daemon + Adapter Pattern

**Key decision (fixed):** `pre_tool.py` is stdlib only and cannot import PyYAML
or any pip dependency. Policy evaluation requires PyYAML. Therefore:

```
pre_tool.py (stdlib only)
    │ sends ToolEvent via Unix socket
    ▼
AgentShield Daemon (long-running process, full deps)
    │
    ├── Policy Engine  (PyYAML rules)
    ├── SQLite Logger  (WAL mode)
    ├── Output Scanner (credential detection)
    └── Session Monitor (loop detection)
    │
    ▼ returns Decision to pre_tool.py
pre_tool.py exits 0 (allow) or 2 (block)
```

**Why daemon, not spawn-per-call:**
- Spawn overhead: ~80-150ms per call (load Python + import modules + connect SQLite)
- Daemon overhead: ~2-5ms per call (Unix socket IPC, already loaded)
- Developer notices > 100ms — daemon is mandatory, not optional

**Daemon lifecycle:**
- Auto-start on first `agentshield install`
- Managed via `launchd` (macOS) or `systemd --user` (Linux)
- `agentshield status` shows daemon health
- Falls back to fail-open if daemon unreachable (see: Fail Behavior below)

---

### System Architecture

```
Any Agent (Claude Code / LangChain / CrewAI / AutoGen / Custom)
    │
    ▼ via Adapter
┌─────────────────────────────────────────────┐
│            AgentShield Daemon               │
│                                             │
│  Policy Engine    →  allow / block          │
│  SQLite Logger    →  every call logged      │
│  Output Scanner   →  credential detect      │
│  Session Monitor  →  loop detection         │
│                                             │
│  Unix socket: /tmp/agentshield.sock         │
└─────────────────────────────────────────────┘
    ↑                           ↑
pre_tool.py              MCP Server / SDK
(Claude Code adapter)    (other adapters)
```

---

### MVP Scope: Claude Code Adapter Only

**Decision (fixed):** MCP Server is NOT in MVP. Removed.

Reason: MCP Server adds complexity before any user validation. Claude Code
adapter validates the core engine. MCP adapter ships only if Week 4 users
explicitly ask for other framework support.

```
MVP adapters:
  ✅ Claude Code Hook (pre_tool.py + post_tool.py)

Post-MVP adapters (only if users ask):
  ⬜ MCP Server adapter (Week 5+ if validated)
  ⬜ Python SDK adapter (Month 2 if validated)
```

---

### Claude Code Adapter Protocol

**stdin from Claude Code:**
```json
{
  "tool_name": "bash",
  "tool_input": { "command": "rm -rf /tmp/test" },
  "session_id": "abc123",
  "hook_event_name": "PreToolUse"
}
```

**pre_tool.py flow (stdlib only):**
```
1. Read stdin JSON
2. Send ToolEvent to daemon via Unix socket (/tmp/agentshield.sock)
3. Receive EngineDecision from daemon
4. If action == "block": print message, exit 2
5. If action == "allow": exit 0
6. If daemon unreachable: exit 0 (fail-open) + log to ~/.agentshield/errors.log
```

**Exit codes:**
- `exit 0` → allow tool to run
- `exit 2` → block tool (stdout shown to agent as error)
- `exit 1` → hook failed (Claude Code logs, continues — avoid this)

**Hook config:** `~/.claude/settings.json`
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

---

### Core Engine Interface

```python
# agentshield/engine/core.py

from dataclasses import dataclass

@dataclass
class ToolEvent:
    tool_name: str      # "bash", "read", "write", "edit", etc.
    tool_input: dict    # tool arguments
    session_id: str     # agent session ID
    agent_id: str       # which agent (future: multi-agent)
    framework: str      # "claude_code" | "mcp" | "sdk"
    timestamp: str      # ISO format

@dataclass
class EngineDecision:
    action: str         # "allow" or "block"
    reason: str | None  # policy rule name if blocked
    message: str | None # message shown to agent if blocked

class AgentShieldEngine:
    def evaluate(self, event: ToolEvent) -> EngineDecision:
        # 1. Load policy (cached, hot-reload on file change)
        # 2. Evaluate rules (first-match-wins)
        # 3. Log to SQLite
        # 4. Return decision
        ...
```

---

## File Structure

```
agentshield/                    ← pip install agentshield
    __init__.py
    daemon/
        server.py               ← Long-running daemon (Unix socket server)
        startup.py              ← launchd/systemd registration
    engine/
        core.py                 ← AgentShieldEngine, ToolEvent, EngineDecision
        policy.py               ← YAML rule evaluation, first-match-wins
        logger.py               ← SQLite WAL logging
        scanner.py              ← Credential/PII detection in output
        monitor.py              ← Session monitor, loop detection
    adapters/
        claude_code/
            pre_tool.py         ← PreToolUse hook (stdlib only)
            post_tool.py        ← PostToolUse hook (stdlib only)
            installer.py        ← Writes ~/.claude/settings.json, starts daemon
        mcp_server/             ← NOT in MVP — stubbed for future
            server.py
        sdk/                    ← NOT in MVP — stubbed for future
            wrapper.py
    policy/
        loader.py               ← Load + watch policy.yaml
        defaults.py             ← Safe default rules (shipped with package)
    storage/
        db.py                   ← SQLite WAL operations
        schema.sql              ← DB schema (see below)
    dashboard/
        server.py               ← FastAPI server
        templates/
            index.html          ← Single-file UI
    cli.py                      ← CLI entry point

~/.agentshield/                 ← Runtime files (created on install)
    pre_tool.py                 ← Deployed Claude Code hook
    post_tool.py                ← Deployed Claude Code hook
    policy.yaml                 ← User policy config
    logs.db                     ← SQLite audit log (WAL mode)
    errors.log                  ← Daemon communication errors
    agentshield.sock            ← Unix socket (daemon IPC)

tests/
    test_engine.py
    test_policy.py
    test_daemon.py
    test_adapters.py

pyproject.toml
README.md
CLAUDE.md
```

---

## Database Schema

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

-- Deduplication: prevent double-logging if multiple adapters active
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup
    ON tool_calls(session_id, tool, ts, framework);

CREATE INDEX IF NOT EXISTS idx_ts        ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_session   ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_blocked   ON tool_calls(blocked);
CREATE INDEX IF NOT EXISTS idx_framework ON tool_calls(framework);
```

**Note:** `UNIQUE INDEX idx_dedup` prevents double-logging when multiple
adapters are active for the same session. INSERT OR IGNORE in db.py.

---

## Policy Engine

**File:** `~/.agentshield/policy.yaml`

```yaml
version: 1

rules:
  - name: block_rm_rf
    tool: bash
    match: "rm -rf"
    action: deny
    message: "Dangerous deletion blocked by AgentShield"

  - name: block_sudo_rm
    tool: bash
    match: "sudo rm"
    action: deny

  - name: block_format
    tool: bash
    match: ["mkfs", "dd if=", "> /dev/sd"]
    action: deny

  - name: protect_ssh
    tool: [read, write, edit]
    path_match: ".ssh/"
    action: deny
    message: "SSH directory protected"

  - name: protect_env
    tool: [read, write, edit]
    path_match: [".env", ".env.local", ".env.production"]
    action: deny
    message: ".env files protected"

  - name: protect_secrets
    tool: [read, write, edit]
    path_match: ["id_rsa", "id_ed25519", "*.pem", "*.key"]
    action: deny

  - name: allow_workspace
    tool: "*"
    path_match: "~/projects/"
    action: allow
    priority: 10
```

Evaluation: sort by priority desc → first match wins → default allow.
Policy file is watched for changes — hot-reload without daemon restart.

---

## Fail Behavior

**Decision (noted, not yet validated with users):**

Current choice: **fail-open** (allow on daemon error)

Rationale: AgentShield is a developer tool first, security tool second in MVP.
Blocking developer workflow when daemon is down → immediate uninstall.
Better to allow and log the miss than to block and lose the user.

```
Daemon unreachable → pre_tool.py:
  1. exit 0 (allow tool to run)
  2. append to ~/.agentshield/errors.log
  3. agentshield status shows "daemon offline" warning
```

**⚠️ NOTE FOR FUTURE:** Enterprise customers will expect fail-closed behavior.
When Team/Enterprise tier ships, add `fail_behavior: open|closed` to policy.yaml.
Fail-closed should be the default for any workspace tagged as `production: true`.

---

## CLI Commands

```bash
# Install + start daemon
agentshield install

# Check daemon status
agentshield status

# Show last N tool calls
agentshield logs [--last 20] [--blocked-only] [--since 1h]

# Start dashboard
agentshield dashboard [--port 7432]

# Validate policy
agentshield policy check

# Export audit log
agentshield export [--format json|csv] [--since 7d]

# Daemon management
agentshield daemon start|stop|restart|status
```

---

## Tech Stack

| Layer | Choice | Reason |
|-------|--------|--------|
| Daemon | Python 3.10+ | Long-running, full deps ok |
| Claude Code adapter | stdlib only | Fast spawn, no pip overhead |
| IPC | Unix socket | Fast, local, no network |
| Policy | PyYAML | Simple, readable, hot-reload |
| Storage | SQLite WAL | Local, fast, concurrent reads |
| CLI | Typer | Clean, type-safe |
| Dashboard | FastAPI + plain HTML | No React build step |
| Daemon management | launchd (mac) / systemd --user (linux) | Native, reliable |
| Package | pyproject.toml | Modern Python packaging |

---

## Key Design Decisions

**1. Daemon architecture (fixed)**
pre_tool.py is stdlib only — cannot use PyYAML for policy evaluation.
Daemon is a long-running process that holds all deps in memory.
IPC via Unix socket: ~2-5ms overhead vs ~100ms+ for spawn-per-call.

**2. Fail-open in MVP (noted)**
Developer tool priority: don't break workflow. Fail-closed is a future
enterprise option configurable per workspace. See Fail Behavior section.

**3. MCP Server NOT in MVP (fixed)**
Scope creep removed. Ships only if Week 4 users ask for it explicitly.

**4. Deduplication via UNIQUE INDEX (fixed)**
Prevents double-logging if multiple adapters active for same session.
INSERT OR IGNORE in db.py handles this transparently.

**5. framework column from day one (fixed)**
Every log row has framework="claude_code" in MVP. Enables cross-framework
analytics when MCP/SDK adapters ship. Schema migration later is painful.

**6. Policy hot-reload (fixed)**
Daemon watches policy.yaml with watchdog or polling. No restart required
when developer edits policy. Reload on file change, debounced 500ms.

**7. Observer now, runtime later (honest)**
MVP cannot prevent bypass if agent doesn't route through hooks.
Runtime (Docker sandbox, no bypass) is Month 9+.
Do not claim runtime capabilities in documentation or marketing.

---

## MVP Scope

**In:**
```
✅ AgentShield Daemon (long-running, Unix socket server)
✅ Claude Code adapter (pre_tool.py + post_tool.py, stdlib only)
✅ Policy engine (YAML, first-match-wins, hot-reload)
✅ SQLite audit log (WAL, deduplication, framework column)
✅ Output credential scanner (PostToolUse)
✅ CLI: install, logs, status, daemon, dashboard
✅ Dashboard: localhost:7432, timeline, blocked calls
✅ PyPI: pip install agentshield
```

**Out:**
```
❌ MCP Server adapter (post-MVP, only if users ask)
❌ Python SDK adapter (Month 2, only if users ask)
❌ Circuit breaker / loop detection (Month 2)
❌ Prompt injection scanner (Month 3)
❌ Cloud sync (Month 2)
❌ Team features (Month 2)
❌ Payments / Stripe (Month 2)
❌ AI-powered detection (Month 4)
❌ Compliance export / SOC2 (Month 6+)
❌ Fail-closed mode (Team/Enterprise tier)
❌ Docker sandbox runtime (Month 9+)
```

---

## Roadmap

```
Week 1:   Daemon core + Claude Code adapter
          Goal: logs.db has first real entry from live Claude Code session
          Bench: pre_tool.py → daemon → decision in < 20ms total

Week 2:   CLI + Dashboard + install experience
          Goal: pip install agentshield && agentshield install
                works on a clean machine in under 2 minutes

Week 3:   PyPI publish + README + demo GIF + distribution
          Post: r/ClaudeAI, r/AIAgents, HN Show HN
          Goal: 50 installs

Week 4:   Talk to 5 users (no pitching, only listening)
          Questions: "What made you install?" + "What would you pay for?"
          Decision gate:
            3+ clear pain descriptions → build team features
            1+ payment offer → build Stripe immediately
            MCP requests → build MCP adapter
            0 pain → change positioning, not product

Month 2:  Python SDK + circuit breaker + team features + Stripe
Month 3:  Prompt injection scanner + LangChain integration
Month 4:  Anomaly detection v1
Month 6:  SOC2 compliance export + first enterprise conversation
Month 9+: Docker sandbox runtime
Year 2:   Full OWASP Top 10 + infrastructure standard
```

---

## Monetization

```
Free      Local only, 7-day retention, 1 agent
          Goal: adoption + word of mouth

Pro       $9/mo — unlimited history, cloud backup, alerts, 3 agents
          NOTE: pricing not validated — adjust after Week 4 conversations

Team      $49/mo per workspace — shared dashboard, shared policy,
          attribution per developer, fail-closed mode option
          NOTE: pricing not validated

Enterprise Custom — SOC2 export, SSO, SLA, on-prem, fail-closed default
          NOTE: need SOC2 expert input before building (Month 5)
```

**⚠️ Pricing note:** Security tools typically price higher than dev tools.
Snyk Team: $52/5 devs. Datadog: $31/host. Validate pricing in Week 4
conversations before putting numbers on landing page.

---

## Moat

**Moat 1 — Cross-platform behavioral data**
AgentShield sees all frameworks. Anthropic sees only Claude. OpenAI sees only GPT.
`framework` column enables this from day one. Collect opt-in telemetry from install 1.

**Moat 2 — Compliance workflow lock-in**
Once security team integrates AgentShield into SOC2 process, switching cost
is organizational inertia. Build compliance features early (Month 6).
⚠️ Need SOC2 expert input on evidence format before building.

**Moat 3 — Policy template network effect**
1,000 teams = 1,000 policy rule sets. Policy Template Marketplace: fintech,
healthcare, startup rules. Value grows with users.

---

## Go-To-Market

**Phase 1 — Seeding (Week 3-4):** 50 installs
- Reddit: r/ClaudeAI, r/AIAgents, r/LocalLLaMA (story format, not announcement)
- HN Show HN
- Discord: Claude Code, LangChain, OpenClaw (question first, link when asked)
- Angle: "After reading Agents of Chaos paper + OWASP Top 10 for Agentic AI —
  I built the tool that implements their recommendations."

**Phase 2 — Content Engine (Month 2):** 500 installs
- Data series from real behavioral data: "What AI agents actually do when
  you're not watching" — unique asset no one else has

**Phase 3 — Partnerships (Month 3-4):** 1,000 installs
- ClawHub (OpenClaw skills — 310k users)
- LangChain docs integration
- Dev newsletters: TLDR, Bytes.dev, Python Weekly

**Phase 4 — Enterprise (Month 5-8):** First contract
- 1 advisor with CISO/DevSecOps background before Month 4
- Target: companies finishing SOC2 Type 1, working toward Type 2

---

## Risks

| Risk | Prob | Mitigation |
|------|------|------------|
| Anthropic ships native audit/policy | High | Cross-platform data moat |
| Zero user validation so far | Critical | Week 4: 5 conversations minimum |
| No enterprise network | High | Security advisor before Month 4 |
| Funded competitor enters | Medium | Ship Week 2, accumulate data first |
| Daemon performance > 50ms | Medium | Bench Day 1, optimize before shipping |
| Fail-open alienates security users | Medium | Add fail-closed in Team tier |
| SOC2 export built wrong format | Medium | Expert input before Month 6 build |
| Hooks API deprecation | Low | Abstract adapter layer |

---

## Testing Checklist

```
Daemon:
[ ] Daemon starts, creates Unix socket at /tmp/agentshield.sock
[ ] Daemon receives ToolEvent, returns EngineDecision in < 5ms
[ ] Daemon hot-reloads policy.yaml on file change
[ ] Daemon restarts via launchd/systemd on crash

Policy engine:
[ ] Rules evaluate in correct priority order
[ ] First-match-wins confirmed with test cases
[ ] Default allow when no rule matches

SQLite:
[ ] Every event logged with framework column
[ ] UNIQUE INDEX prevents duplicates (INSERT OR IGNORE)
[ ] WAL mode confirmed (PRAGMA journal_mode returns "wal")
[ ] Dashboard reads while hook writes — no lock

Claude Code adapter:
[ ] pre_tool.py stdlib only (grep for imports, none outside stdlib)
[ ] pre_tool.py → daemon → decision in < 20ms total
[ ] rm -rf → exit 2 with message in < 20ms
[ ] Normal file read → exit 0 in < 20ms
[ ] Daemon unreachable → exit 0 + errors.log entry (fail-open)

Install:
[ ] agentshield install writes correct settings.json
[ ] agentshield install starts daemon
[ ] pip install agentshield + install works in < 2 minutes clean machine

Dashboard:
[ ] Timeline shows tool calls with blocked status
[ ] Blocked calls highlighted
[ ] agentshield logs CLI matches dashboard data
```

---

## Known Issues / TODOs

```
BEFORE CODING:
  TODO: Validate fail-open choice with first 5 users (may need to flip)
  TODO: Decide daemon IPC: Unix socket vs named pipe (Windows compat)

WEEK 1:
  TODO: Bench pre_tool.py → daemon round trip, must be < 20ms
  TODO: Session ID extraction from Claude Code env vars (undocumented)
  TODO: Daemon launchd plist for macOS auto-start
  TODO: Daemon systemd --user service for Linux auto-start

WEEK 2:
  TODO: post_tool.py credential detection patterns (regex library)
  TODO: Windows/WSL path normalization in policy matching

MONTH 2:
  TODO: Circuit breaker implementation (call count + time window)
  TODO: MCP Server (only if Week 4 users ask for it)
  TODO: Stripe integration

MONTH 6:
  TODO: Talk to SOC2 expert before building compliance export
  TODO: Understand auditor's exact evidence format requirements
```

---

## Resources

- Claude Code Hooks: https://docs.anthropic.com/en/docs/claude-code/hooks
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- OWASP Agentic AI Top 10: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- Agents of Chaos paper: https://arxiv.org/abs/2602.20021
- OpenClaw security docs: https://docs.openclaw.ai/gateway/security

---

*Last updated: March 2026*
*Phase: MVP — Week 1*
*Status: pre-build*
*Next action: build daemon/server.py + adapters/claude_code/pre_tool.py*
*Next milestone: first real tool call logged to logs.db in < 20ms*
