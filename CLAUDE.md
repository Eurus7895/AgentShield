# CLAUDE.md — AgentShield

> Context anchor for every coding session. Read this file before doing anything.
> This file contains the complete project context — strategy, architecture, roadmap, decisions.

---

## One Sentence

AgentShield is the governance and compliance layer for AI agents —
intercept every tool call, enforce policy, log audit trail, detect anomalies.
Regardless of where agents run — Claude Code, OpenSandbox, LangChain, or production servers —
AgentShield knows what they're doing and controls what they're allowed to do.

---

## Positioning — Critical Distinction

```
OpenSandbox:   "Can this code run safely?"         → Execution isolation
AgentShield:   "Should this agent be allowed to    → Governance + compliance
                do this, and what did it do?"
```

**OpenSandbox is Docker for agents. AgentShield is CloudTrail + IAM Policy for agents.**

These are complementary, not competing.
OpenSandbox provides the isolation runtime. AgentShield provides the audit + policy + compliance layer on top.
A team using OpenSandbox still needs to know: "What is our agent doing inside that sandbox?"

**Do NOT position AgentShield as a runtime or sandbox.**
Alibaba already built that — better, with more resources, open source under Apache 2.0.
AgentShield's territory is governance, visibility, and compliance. That gap is real and unfilled.

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

OpenSandbox solves isolation. AgentShield solves governance.
Both are needed. Neither replaces the other.

---

## What AgentShield Is — Honest Definition

**Current MVP: Security Observer + Policy Engine**

AgentShield intercepts agent tool calls via hooks, enforces policy rules,
logs a complete audit trail, and detects anomalous behavior.

```
Agent (anywhere)
    │
    ▼ via Adapter (Hook / MCP / SDK)
┌──────────────────────────────────────┐
│         AgentShield Engine           │
│                                      │
│  Policy Engine   → allow / block     │
│  Audit Logger    → what happened     │
│  Output Scanner  → credential leak   │
│  Session Monitor → loop detection    │
└──────────────────────────────────────┘
```

**Known limitation:** Agent can bypass if it doesn't route through hook/MCP.
This is the tradeoff for fast MVP. Accepted.

**Long-term vision: Universal Agent Governance**
AgentShield becomes the standard governance layer for all agents —
regardless of framework, runtime, or cloud provider.
Works alongside OpenSandbox, not against it.

**Path:** Observer (MVP) → Compliance Layer (v2) → Universal Governance Standard (v3)

---

## Why Now

```
Dec 2025:  OWASP Top 10 for Agentic AI published
Jan 2026:  OpenClaw — 512 vulnerabilities, 135,000 exposed instances
Mar 2026:  OpenSandbox (Alibaba) — solves isolation, NOT governance
Mar 2026:  AgentShield — fills the governance gap OpenSandbox left open
6-12mo:    Anthropic/OpenAI ship native audit → window for independent layer narrows
```

**The gap OpenSandbox confirmed:** They built a world-class sandbox but shipped
zero policy engine, zero compliance export, zero behavioral audit trail.
That is AgentShield's market — and OpenSandbox's 3,845 stars in 2 days
validated that the market is real and ready.

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

MVP covers 4/10. Layer 2 adds 3 more. Layer 3 completes the set.

OpenSandbox covers none of these — it solves execution isolation (OWASP #2 partially),
but does not provide policy engine, audit trail, or compliance tooling.

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
- Spawn overhead: ~80-150ms per call
- Daemon overhead: ~2-5ms per call (Unix socket IPC)
- Developer notices > 100ms — daemon is mandatory

**Daemon lifecycle:**
- Auto-start on `agentshield install`
- Managed via `launchd` (macOS) or `systemd --user` (Linux)
- Falls back to fail-open if daemon unreachable

---

### Adapter Layer

**Adapter 1 — Claude Code Hook (ships Week 1)**
Official `PreToolUse` / `PostToolUse` Hooks API.

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

Hook protocol:
- stdin: `{ "tool_name": "bash", "tool_input": {...}, "session_id": "abc" }`
- exit 0 → allow | exit 2 → block | exit 1 → hook error

**Adapter 2 — MCP Server (post-MVP, only if Week 4 users ask)**
Any MCP-compatible agent routes through AgentShield MCP Server.

**Adapter 3 — Python SDK (Month 2, only if Week 4 users ask)**
`@shield.protect()` decorator for LangChain, CrewAI, custom agents.

**Adapter 4 — OpenSandbox Integration (Month 3+)**
AgentShield governance layer wrapping OpenSandbox execution environment.
Agents run in OpenSandbox (isolation) + monitored by AgentShield (governance).
This is the complementary stack: OpenSandbox for WHERE, AgentShield for WHAT.

---

### Core Engine Interface

```python
@dataclass
class ToolEvent:
    tool_name: str      # "bash", "read", "write", etc.
    tool_input: dict    # tool arguments
    session_id: str     # agent session ID
    agent_id: str       # which agent
    framework: str      # "claude_code" | "mcp" | "sdk" | "opensandbox"
    timestamp: str      # ISO format

@dataclass
class EngineDecision:
    action: str         # "allow" or "block"
    reason: str | None  # rule name if blocked
    message: str | None # shown to agent if blocked
```

---

## File Structure

```
agentshield/
    __init__.py
    daemon/
        server.py               ← Long-running daemon (Unix socket server)
        startup.py              ← launchd/systemd registration
    engine/
        core.py                 ← AgentShieldEngine, ToolEvent, EngineDecision
        policy.py               ← YAML rule evaluation, first-match-wins
        logger.py               ← SQLite WAL logging
        scanner.py              ← Credential/PII detection
        monitor.py              ← Session monitor, loop detection
    adapters/
        claude_code/
            pre_tool.py         ← PreToolUse hook (stdlib only)
            post_tool.py        ← PostToolUse hook (stdlib only)
            installer.py        ← Writes ~/.claude/settings.json, starts daemon
        mcp_server/             ← Post-MVP stub
            server.py
        sdk/                    ← Post-MVP stub
            wrapper.py
        opensandbox/            ← Month 3+ stub
            integration.py
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

-- Deduplication: prevent double-logging
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup
    ON tool_calls(session_id, tool, ts, framework);

CREATE INDEX IF NOT EXISTS idx_ts        ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_session   ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_blocked   ON tool_calls(blocked);
CREATE INDEX IF NOT EXISTS idx_framework ON tool_calls(framework);
```

`framework` column — tracks which adapter generated each event.
Foundation for cross-framework governance analytics (the data moat).

---

## Policy Engine

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
Policy hot-reload: daemon watches policy.yaml, reloads on change (debounced 500ms).

---

## Fail Behavior

**Current choice: fail-open (allow on daemon error)**

Rationale: developer tool first — don't break workflow.

```
Daemon unreachable → pre_tool.py:
  1. exit 0 (allow)
  2. append to ~/.agentshield/errors.log
  3. agentshield status shows "daemon offline"
```

**⚠️ Future:** Add `fail_behavior: open|closed` in policy.yaml.
Enterprise workspaces tagged `production: true` should default fail-closed.

---

## CLI Commands

```bash
agentshield install             # Install hooks + start daemon
agentshield status              # Check daemon health
agentshield logs [--last 20] [--blocked-only] [--since 1h]
agentshield dashboard [--port 7432]
agentshield policy check
agentshield export [--format json|csv] [--since 7d]
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
| Daemon mgmt | launchd / systemd --user | Native, reliable |
| Package | pyproject.toml | Modern Python packaging |

---

## Key Design Decisions

**1. Governance positioning, not runtime (updated)**
AgentShield does not compete with OpenSandbox. Governance ≠ isolation.
The question is not "can this run safely?" but "should this agent be allowed
to do this, and what did it actually do?"

**2. Daemon architecture (fixed)**
stdlib-only `pre_tool.py` cannot evaluate YAML policy. Daemon holds full deps.
IPC via Unix socket: ~2-5ms vs ~100ms spawn-per-call.

**3. Fail-open in MVP (noted)**
Flip to fail-closed in Team/Enterprise tier via policy.yaml config.

**4. MCP + SDK post-MVP only (fixed)**
Ships only if Week 4 users explicitly ask. Scope creep removed.

**5. OpenSandbox as integration target, not competitor (updated)**
AgentShield + OpenSandbox = complete agent security stack.
OpenSandbox: WHERE agents run safely.
AgentShield: WHAT agents are allowed to do and audit of what they did.

**6. Deduplication via UNIQUE INDEX (fixed)**
Prevents double-logging when multiple adapters active.

**7. framework column from day one (fixed)**
Enables cross-framework governance analytics — the data moat.

**8. Policy: first-match-wins, deny dangerous, allow rest**
Simple and predictable. Safe out of the box.

---

## MVP Scope

**In:**
```
✅ AgentShield Daemon (long-running, Unix socket)
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
❌ OpenSandbox integration (Month 3+)
❌ Circuit breaker / loop detection (Month 2)
❌ Prompt injection scanner (Month 3)
❌ Cloud sync (Month 2)
❌ Team features (Month 2)
❌ Payments (Month 2)
❌ AI-powered detection (Month 4)
❌ Compliance export / SOC2 (Month 6+)
❌ Fail-closed mode (Team/Enterprise tier)
❌ Docker sandbox runtime — NOT in roadmap (OpenSandbox already owns this)
```

---

## Roadmap

```
Week 1:   Daemon core + Claude Code adapter
          Goal: logs.db has first real entry in < 20ms

Week 2:   CLI + Dashboard + install experience
          Goal: pip install agentshield works in 2 minutes

Week 3:   PyPI + README + demo GIF
          Post: r/ClaudeAI, r/AIAgents, HN Show HN
          Goal: 50 installs

Week 4:   Talk to 5 users
          Questions: "What made you install?" + "What would you pay for?"
          Gate: 3+ pain descriptions → team features
                1+ payment offer → build Stripe immediately
                MCP requests → build MCP adapter
                OpenSandbox users → build OpenSandbox integration

Month 2:  SDK + circuit breaker + team features + Stripe
Month 3:  Prompt injection scanner + OpenSandbox integration
Month 4:  Anomaly detection v1 + LangChain
Month 6:  SOC2 compliance export + first enterprise conversation
Year 1:   Universal governance standard — works with any runtime
Year 2:   Full OWASP Top 10 coverage
```

---

## Monetization

```
Free      Local only, 7-day retention, 1 agent
          Goal: adoption + word of mouth

Pro       $9/mo — unlimited history, cloud backup, alerts, 3 agents
          NOTE: not validated — adjust after Week 4

Team      $49/mo per workspace — shared dashboard, shared policy,
          attribution per developer, fail-closed option
          NOTE: not validated

Enterprise Custom ($500-2000/mo) — SOC2 export, SSO, SLA, on-prem
          NOTE: need SOC2 expert before building (Month 5)
```

**⚠️ Pricing note:** Security/governance tools price higher than dev tools.
Validate pricing in Week 4 before putting numbers on landing page.

---

## Competitive Landscape

| Tool | What it does | Gap vs AgentShield |
|------|-------------|-------------------|
| **OpenSandbox (Alibaba)** | Execution isolation, container runtime | No policy engine, no audit trail, no compliance |
| LangSmith | Observability for LangChain | No enforcement, no blocking, LangChain-only |
| Portkey | LLM gateway + guardrails | API-level only, not tool-call level |
| Helicone | LLM usage analytics | Cost tracking only, no security |
| Invariant | Agent testing | Pre-deployment, not runtime enforcement |

**AgentShield's unique position:**
- Only tool focused on governance + compliance (not just isolation or observability)
- Works alongside OpenSandbox — the two tools complete each other
- Cross-framework behavioral data — sees Claude Code + LangChain + OpenSandbox simultaneously

---

## Moat

**Moat 1 — Cross-framework governance data**
AgentShield sees all frameworks. Anthropic sees Claude only. OpenAI sees GPT only.
OpenSandbox sees their sandbox only. `framework` column enables cross-framework
behavioral analytics from day one. No one else can replicate this dataset.

**Moat 2 — Compliance workflow lock-in**
Once security team integrates AgentShield into SOC2/GDPR process,
switching cost is organizational inertia. Build compliance features early.
⚠️ Talk to SOC2 expert before building compliance export (Month 5).

**Moat 3 — Policy template network effect**
1,000 teams = 1,000 policy sets. Policy Template Marketplace by framework:
fintech rules, healthcare rules, OpenSandbox-specific rules. Value grows with users.

---

## Go-To-Market

**Phase 1 — Seeding (Week 3-4):** 50 installs
- Reddit: r/ClaudeAI, r/AIAgents, r/LocalLLaMA
- HN Show HN
- Discord: Claude Code, LangChain, OpenClaw
- Angle: "OpenSandbox solves isolation. I built the governance layer it doesn't have."

**Phase 2 — Content Engine (Month 2):** 500 installs
- "What AI agents actually do when you're not watching"
- Real behavioral data — no one else has this

**Phase 3 — OpenSandbox integration (Month 3):** 1,000 installs
- Submit to OpenSandbox ecosystem / examples
- Position as the natural companion tool
- Joint distribution with OpenSandbox community

**Phase 4 — Enterprise (Month 5-8):** First contract
- 1 advisor with CISO/DevSecOps background before Month 4
- Target: companies using OpenSandbox that need compliance on top

---

## Risks

| Risk | Prob | Mitigation |
|------|------|------------|
| Anthropic ships native audit/policy | High | Cross-framework data moat |
| OpenSandbox adds governance features | Medium | They focus on isolation — governance is different domain |
| Zero user validation | Critical | Week 4: 5 conversations before building more |
| No enterprise network | High | Security advisor before Month 4 |
| Funded competitor enters | Medium | Ship Week 2, get users, accumulate data |
| Daemon performance > 50ms | Medium | Bench Day 1 |
| Fail-open alienates security users | Medium | Fail-closed in Team tier |
| SOC2 export built wrong format | Medium | Expert input before Month 6 |

---

## Testing Checklist

```
Daemon:
[ ] Starts, creates /tmp/agentshield.sock
[ ] Receives ToolEvent, returns EngineDecision in < 5ms
[ ] Hot-reloads policy.yaml on change
[ ] Restarts via launchd/systemd on crash

Policy engine:
[ ] Rules evaluate in correct priority order
[ ] First-match-wins confirmed
[ ] Default allow when no rule matches

SQLite:
[ ] Every event logged with framework column
[ ] UNIQUE INDEX prevents duplicates (INSERT OR IGNORE)
[ ] WAL mode confirmed
[ ] Dashboard reads while hook writes — no lock

Claude Code adapter:
[ ] pre_tool.py stdlib only (no non-stdlib imports)
[ ] pre_tool.py → daemon → decision in < 20ms
[ ] rm -rf → exit 2 in < 20ms
[ ] Normal file read → exit 0 in < 20ms
[ ] Daemon unreachable → exit 0 + errors.log (fail-open)

Install:
[ ] agentshield install writes settings.json + starts daemon
[ ] pip install + install works in < 2 min on clean machine

Dashboard:
[ ] Timeline with blocked highlights
[ ] agentshield logs matches dashboard data
```

---

## TODOs

```
BEFORE CODING:
  TODO: Validate fail-open with first 5 users
  TODO: Decide IPC: Unix socket vs named pipe (Windows compat)

WEEK 1:
  TODO: Bench pre_tool.py → daemon round trip (target < 20ms)
  TODO: Session ID from Claude Code env vars
  TODO: launchd plist (macOS) + systemd --user (Linux)

WEEK 2:
  TODO: post_tool.py credential detection patterns
  TODO: Windows/WSL path normalization

MONTH 2:
  TODO: Circuit breaker (call count + time window)
  TODO: MCP Server (only if Week 4 users ask)
  TODO: Stripe integration

MONTH 3:
  TODO: OpenSandbox integration design
  TODO: How AgentShield hooks into OpenSandbox execution events

MONTH 6:
  TODO: SOC2 expert consultation before building compliance export
```

---

## Resources

- Claude Code Hooks: https://docs.anthropic.com/en/docs/claude-code/hooks
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- OWASP Agentic AI Top 10: https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- Agents of Chaos paper: https://arxiv.org/abs/2602.20021
- OpenSandbox (Alibaba): https://github.com/alibaba/OpenSandbox
- OpenClaw security docs: https://docs.openclaw.ai/gateway/security

---

*Last updated: March 2026*
*Phase: MVP — Week 1*
*Status: pre-build*
*Positioning: Governance + Compliance layer (not runtime — OpenSandbox owns that)*
*Next action: build daemon/server.py + adapters/claude_code/pre_tool.py*
*Next milestone: first real tool call logged to logs.db in < 20ms*
