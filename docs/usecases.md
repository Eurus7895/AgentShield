# AgentShield — Use Cases

> Last updated: 2026-04-10

---

## Who Is AgentShield For?

AgentShield serves anyone running AI agents that interact with tools — file
systems, shells, APIs, databases — and needs visibility, control, or compliance
over what those agents do.

---

## Use Case 1: Prevent Destructive Operations

**Persona:** Solo developer using Claude Code for daily coding tasks.

**Problem:** An AI agent can run `rm -rf /`, `sudo rm`, `mkfs`, or overwrite
critical config files. One bad tool call can destroy hours of work or brick a
system.

**How AgentShield helps:**
- Policy rules block dangerous bash commands (`rm -rf`, `sudo rm`, `dd if=`)
  before they execute.
- The `PreToolUse` hook intercepts the call and returns `exit 2` (block) in < 20ms.
- The agent sees a clear message: *"Dangerous deletion blocked by AgentShield."*

**Example policy rule:**
```yaml
- name: block_rm_rf
  tool: bash
  match: "rm -rf"
  action: deny
  message: "Dangerous deletion blocked by AgentShield"
```

---

## Use Case 2: Protect Sensitive Files

**Persona:** Developer working on a project with secrets, SSH keys, and
environment variables alongside AI agents.

**Problem:** Agents can read `.env` files, SSH private keys, API credentials,
and certificates — either by direct request or through prompt injection.

**How AgentShield helps:**
- Path-based policy rules deny read/write/edit access to sensitive file patterns.
- Protects `.ssh/`, `.env`, `*.pem`, `*.key`, `id_rsa`, and `id_ed25519` by default.

**Example policy rules:**
```yaml
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
```

---

## Use Case 3: Detect Credential Leaks in Output

**Persona:** Developer whose agent generates code, config files, or shell output
that may inadvertently contain secrets.

**Problem:** An agent's output might include AWS keys, API tokens, database
passwords, or private keys — exposed in the conversation or written to files.

**How AgentShield helps:**
- The `PostToolUse` hook scans agent output for known credential patterns
  (AWS keys, GitHub tokens, private key headers, etc.).
- Flagged events are logged to the audit trail with a warning.
- Addresses **OWASP Agentic AI #5: Insecure Output Handling**.

---

## Use Case 4: Full Audit Trail of Agent Activity

**Persona:** Team lead or security engineer who needs to know what AI agents
did across a project.

**Problem:** Agents run dozens to hundreds of tool calls per session. Without
logging, there is no way to reconstruct what happened, debug issues, or
satisfy compliance requirements.

**How AgentShield helps:**
- Every tool call is logged to SQLite with timestamp, session ID, agent ID,
  tool name, input, blocked status, and framework.
- `agentshield logs` provides CLI access to the audit trail.
- `agentshield dashboard` shows a visual timeline with blocked call highlights.
- `agentshield export --format json` enables integration with external SIEM tools.

**Example CLI usage:**
```bash
agentshield logs --last 20
agentshield logs --blocked-only --since 1h
agentshield export --format csv --since 7d
```

---

## Use Case 5: Cross-Framework Governance

**Persona:** Organization running agents across Claude Code, LangChain,
CrewAI, and potentially OpenSandbox.

**Problem:** Each framework has its own logging (or none at all). There is no
single view of what all agents are doing across the organization.

**How AgentShield helps:**
- The `framework` column in every log entry tracks which adapter generated
  the event (`claude_code`, `mcp`, `sdk`, `opensandbox`).
- A single dashboard and audit trail covers all agent frameworks.
- Policy rules apply consistently regardless of which framework the agent uses.

---

## Use Case 6: Workspace Scoping

**Persona:** Developer working on multiple projects who wants agents to stay
within project boundaries.

**Problem:** An agent working in `~/projects/app-a` should not be reading or
modifying files in `~/projects/app-b` or system directories.

**How AgentShield helps:**
- Policy rules can scope allowed operations to specific directories.
- Anything outside the allowed workspace is denied by default.

**Example policy rule:**
```yaml
- name: allow_workspace
  tool: "*"
  path_match: "~/projects/app-a/"
  action: allow
  priority: 10
```

---

## Use Case 7: Session Replay and Debugging

**Persona:** Developer debugging an agent session that went wrong — infinite
loops, unexpected file modifications, or mysterious failures.

**Problem:** Agent sessions can be long and complex. Without a timeline, it is
hard to identify where things went wrong.

**How AgentShield helps:**
- The dashboard provides a session-scoped timeline of every tool call.
- Blocked calls are highlighted, making it easy to spot policy violations.
- Session metadata (tool count, block count, duration) gives an at-a-glance summary.
- Addresses **OWASP Agentic AI #9: False Completion** — replay confirms whether
  the agent actually completed its task.

---

## Use Case 8: Compliance and Reporting

**Persona:** Security team preparing for SOC2 audit or GDPR review where AI
agents access production data.

**Problem:** Auditors need evidence of what AI agents accessed, what was blocked,
and what controls are in place. No existing tool provides this.

**How AgentShield helps:**
- Structured audit logs exportable as JSON or CSV.
- Policy YAML serves as a documented control specification.
- Dashboard provides visual evidence of enforcement.
- Roadmap includes dedicated SOC2 compliance export (Month 6).

---

## OWASP Agentic AI Coverage

AgentShield addresses the following risks from the
[OWASP Top 10 for Agentic Applications (2026)](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/):

| # | OWASP Risk | AgentShield Response | Status |
|---|-----------|---------------------|--------|
| 1 | Goal Hijack | Imperative-language detection in output scanner | **Shipped** (partial) |
| 2 | Tool Misuse | Policy engine + PreToolUse blocking | **Shipped** |
| 3 | Identity Abuse | Per-agent-role policies via agent_id/agent_type | **Shipped** (partial) |
| 4 | Delegated Authority | Provenance ledger with source_event_id | **Shipped** (partial) |
| 5 | Insecure Output | PostToolUse credential scanning (7 patterns) | **Shipped** |
| 6 | Memory Poisoning | Memory guardian — write-protect MEMORY.md | **Shipped** |
| 7 | Multi-Agent Cascade | Session isolation + cross-agent audit | Planned |
| 8 | Infinite Loop | Sliding-window loop detection (30 calls/10s) | **Shipped** |
| 9 | False Completion | Session replay timeline | Week 2 (needs dashboard) |
| 10 | Semantic Bypass | LLM-powered intent analysis | Planned |
