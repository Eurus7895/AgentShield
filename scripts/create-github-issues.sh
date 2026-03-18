#!/usr/bin/env bash
# Creates GitHub labels + issues for AgentShield development tracking.
# Requires: gh CLI authenticated (gh auth login)
# Usage: bash scripts/create-github-issues.sh

set -euo pipefail

REPO="Eurus7895/AgentShield"

echo "=== Creating labels ==="

# Severity labels (match CLAUDE.md threat severity levels)
gh label create "sev-0-info"     --color "c5def5" --description "Info — normal operations"          --repo "$REPO" --force
gh label create "sev-1-low"      --color "bfd4f2" --description "Low risk"                          --repo "$REPO" --force
gh label create "sev-2-medium"   --color "fbca04" --description "Medium risk"                       --repo "$REPO" --force
gh label create "sev-3-high"     --color "e99695" --description "High risk — blocks release"        --repo "$REPO" --force
gh label create "sev-4-critical" --color "d93f0b" --description "Critical — system-level damage"    --repo "$REPO" --force
gh label create "sev-5-exploit"  --color "b60205" --description "Exploit — credential/secret risk"  --repo "$REPO" --force

# Milestone labels
gh label create "week-1"   --color "0e8a16" --description "Week 1 milestone"    --repo "$REPO" --force
gh label create "week-2"   --color "0e8a16" --description "Week 2 milestone"    --repo "$REPO" --force
gh label create "week-3"   --color "0e8a16" --description "Week 3 milestone"    --repo "$REPO" --force
gh label create "month-2+" --color "006b75" --description "Month 2+ roadmap"    --repo "$REPO" --force

# Category labels
gh label create "decision"    --color "d4c5f9" --description "Architecture decision needed" --repo "$REPO" --force
gh label create "performance" --color "fef2c0" --description "Performance-related"          --repo "$REPO" --force

echo ""
echo "=== Creating issues ==="

# Issue 1: Daemon server
gh issue create --repo "$REPO" \
  --title "Daemon server — Unix socket IPC + ToolEvent/Decision protocol" \
  --label "sev-2-medium,week-1" \
  --body "$(cat <<'EOF'
## Summary
Implement `daemon/server.py` — the long-running AgentShield daemon process.

## Requirements
- Listen on Unix socket at `~/.agentshield/agentshield.sock`
- Receive `ToolEvent` JSON from hook scripts (pre_tool.py / post_tool.py)
- Evaluate policy, log to SQLite, return `EngineDecision` JSON
- Target: < 5ms IPC round-trip latency

## Technical Details
- Uses `engine/core.py` dataclasses (`ToolEvent`, `EngineDecision`)
- Integrates policy engine, SQLite logger, output scanner
- Must handle concurrent connections (multiple agent sessions)
- Graceful shutdown on SIGTERM/SIGINT

## Files
- `agentshield/daemon/server.py` (main implementation)
- `agentshield/engine/core.py` (protocol dataclasses — already defined)

## References
- CLAUDE.md: Architecture section, "Daemon + Adapter Pattern"
- CLAUDE.md: Core Engine Interface
EOF
)"

# Issue 2: Policy engine
gh issue create --repo "$REPO" \
  --title "Policy engine — YAML rules, first-match-wins evaluation" \
  --label "sev-2-medium,week-1" \
  --body "$(cat <<'EOF'
## Summary
Implement the YAML-based policy engine for tool call evaluation.

## Requirements
- Load rules from `~/.agentshield/policy.yaml`
- Sort rules by priority (descending), first-match-wins
- Default: allow (when no rule matches)
- Support match types: `match` (string/list), `path_match` (glob patterns)
- Hot-reload policy file on change (debounced 500ms)

## Default Rules (ship with install)
- `block_rm_rf`: deny `rm -rf` in bash
- `block_sudo_rm`: deny `sudo rm` in bash
- `block_format`: deny `mkfs`, `dd if=`, `> /dev/sd` in bash
- `protect_ssh`: deny read/write/edit on `.ssh/`
- `protect_env`: deny read/write/edit on `.env*`
- `protect_secrets`: deny read/write/edit on `id_rsa`, `*.pem`, `*.key`

## Files
- `agentshield/engine/policy.py` (rule evaluation)
- `agentshield/policy/loader.py` (YAML loading + hot-reload)
- `agentshield/policy/defaults.py` (default policy content)
- `tests/test_policy.py` (unit tests)

## References
- CLAUDE.md: Policy Engine section
- CLAUDE.md: Threat Severity Levels
EOF
)"

# Issue 3: Claude Code adapter
gh issue create --repo "$REPO" \
  --title "Claude Code adapter — pre_tool.py + post_tool.py (stdlib only)" \
  --label "sev-2-medium,week-1" \
  --body "$(cat <<'EOF'
## Summary
Implement the Claude Code hook scripts that bridge Claude Code → AgentShield daemon.

## Requirements
- **MUST be stdlib only** — no pip dependencies (no PyYAML, no requests, etc.)
- `pre_tool.py`: PreToolUse hook
  - Read tool event from stdin (JSON)
  - Connect to daemon via Unix socket
  - Send ToolEvent, receive EngineDecision
  - Exit 0 = allow, exit 2 = block
  - Fail-open: exit 0 + log to errors.log if daemon unreachable
- `post_tool.py`: PostToolUse hook
  - Read tool output from stdin
  - Send to daemon for credential scanning + audit logging
  - Exit 0 always (post-hook doesn't block)

## Hook Protocol (Claude Code v2.1.78+)
- stdin: `{ "tool_name": "bash", "tool_input": {...}, "session_id": "abc", ... }`
- exit 0 → allow | exit 2 → block | exit 1 → hook error

## Files
- `agentshield/adapters/claude_code/pre_tool.py`
- `agentshield/adapters/claude_code/post_tool.py`
- `tests/test_adapters.py`

## References
- CLAUDE.md: Adapter Layer section
- CLAUDE.md: Fail Behavior section
EOF
)"

# Issue 4: SQLite audit logger
gh issue create --repo "$REPO" \
  --title "SQLite audit logger — WAL mode, dedup index, framework column" \
  --label "sev-2-medium,week-1" \
  --body "$(cat <<'EOF'
## Summary
Implement SQLite-based audit logging for all tool call events.

## Requirements
- WAL mode (concurrent reads while daemon writes)
- UNIQUE INDEX on `(session_id, tool, ts, framework)` for deduplication
- `INSERT OR IGNORE` to prevent double-logging
- `framework` column from day one (enables cross-framework analytics)
- Tables: `tool_calls`, `sessions`

## Schema
Per CLAUDE.md database schema section — `tool_calls` table with id, ts, session_id,
agent_id, framework, tool, input, blocked, reason, duration_ms. `sessions` table
with id, agent_id, framework, started_at, ended_at, tool_count, block_count.

## Files
- `agentshield/storage/db.py` (database operations)
- `agentshield/storage/schema.sql` (CREATE TABLE statements)

## References
- CLAUDE.md: Database Schema section
EOF
)"

# Issue 5: Benchmark
gh issue create --repo "$REPO" \
  --title "Benchmark: pre_tool.py → daemon round trip must be < 20ms" \
  --label "sev-3-high,week-1,performance" \
  --body "$(cat <<'EOF'
## Summary
Create integration test that measures end-to-end latency and gates release.

## Requirements
- Measure: pre_tool.py → Unix socket → daemon → policy eval → response → exit
- Target: < 20ms for both allow and block decisions
- This is a **release gate** — Week 1 cannot ship if latency > 20ms
- Test should run in CI and fail if threshold exceeded

## Test Cases
- `rm -rf /` → blocked in < 20ms
- `cat README.md` → allowed in < 20ms
- Daemon unreachable → fail-open (exit 0) in < 20ms

## Files
- `tests/test_daemon.py` (integration + benchmark tests)

## References
- CLAUDE.md: Testing Checklist
- CLAUDE.md: TODOs → "Bench pre_tool.py → daemon round trip"
EOF
)"

# Issue 6: Daemon lifecycle
gh issue create --repo "$REPO" \
  --title "Daemon lifecycle — launchd (macOS) + systemd --user (Linux)" \
  --label "sev-2-medium,week-1" \
  --body "$(cat <<'EOF'
## Summary
Implement OS-level daemon management for auto-start and crash recovery.

## Requirements
- macOS: launchd plist registration (`~/Library/LaunchAgents/`)
- Linux: systemd --user unit file (`~/.config/systemd/user/`)
- Auto-start on `agentshield install`
- Auto-restart on crash
- Used by `agentshield daemon start/stop/restart/status`

## Files
- `agentshield/daemon/startup.py`

## References
- CLAUDE.md: TODOs → "launchd plist (macOS) + systemd --user (Linux)"
- CLAUDE.md: Architecture → Daemon lifecycle
EOF
)"

# Issue 7: Credential scanner
gh issue create --repo "$REPO" \
  --title "Credential/secret scanner for PostToolUse output" \
  --label "sev-3-high,week-2" \
  --body "$(cat <<'EOF'
## Summary
Implement regex-based credential detection for scanning tool outputs.

## Requirements
- Detect common credential patterns in PostToolUse output:
  - AWS access keys (`AKIA...`)
  - GitHub tokens (`ghp_`, `gho_`, `ghs_`)
  - SSH private key headers (`-----BEGIN.*PRIVATE KEY-----`)
  - Generic API tokens / bearer tokens
  - `.env` style `KEY=value` with sensitive key names
- Return list of findings with pattern name and severity
- Used by `post_tool.py` and daemon PostToolUse handler

## OWASP Coverage
Addresses OWASP #5 — Insecure Output (credential leakage detection)

## Files
- `agentshield/engine/scanner.py`

## References
- CLAUDE.md: TODOs → "post_tool.py credential detection patterns"
- CLAUDE.md: OWASP Coverage table
EOF
)"

# Issue 8: CLI + installer
gh issue create --repo "$REPO" \
  --title "CLI + installer — agentshield install/status/logs/dashboard" \
  --label "sev-2-medium,week-2" \
  --body "$(cat <<'EOF'
## Summary
Implement the Typer-based CLI and Claude Code hook installer.

## Commands
- `agentshield install` — write hooks to `~/.claude/settings.json` + start daemon
- `agentshield status` — check daemon health, show socket status
- `agentshield logs [--last N] [--blocked-only] [--since 1h]` — query audit log
- `agentshield dashboard [--port 7432]` — launch web dashboard
- `agentshield daemon start|stop|restart|status` — manage daemon process
- `agentshield policy check` — validate policy.yaml syntax
- `agentshield export [--format json|csv] [--since 7d]` — export audit data

## Install Requirements
- Must not clobber existing hooks in settings.json (merge, don't overwrite)
- Copy pre_tool.py + post_tool.py to `~/.agentshield/`
- Write default policy.yaml if none exists
- Start daemon
- Target: `pip install agentshield && agentshield install` < 2 minutes

## Files
- `agentshield/cli.py` (Typer CLI)
- `agentshield/adapters/claude_code/installer.py` (hook installer)

## References
- CLAUDE.md: CLI Commands section
EOF
)"

# Issue 9: Dashboard
gh issue create --repo "$REPO" \
  --title "Dashboard — FastAPI + HTML timeline with blocked call highlights" \
  --label "sev-1-low,week-2" \
  --body "$(cat <<'EOF'
## Summary
Implement the localhost web dashboard for viewing tool call audit trail.

## Requirements
- FastAPI server on `localhost:7432`
- Plain HTML + minimal CSS (no React build step)
- Tool call timeline view
- Blocked calls highlighted: sev-3+ = red, sev-1-2 = yellow, sev-0 = grey
- Filter by session, time range, blocked-only
- Data sourced from SQLite audit log

## Files
- `agentshield/dashboard/server.py` (FastAPI app)
- `agentshield/dashboard/templates/index.html` (Jinja2 template)

## References
- CLAUDE.md: Testing Checklist → Dashboard section
EOF
)"

# Issue 10: IPC decision
gh issue create --repo "$REPO" \
  --title "Decision: Unix socket vs named pipe for Windows support" \
  --label "sev-2-medium,decision" \
  --body "$(cat <<'EOF'
## Decision Needed
Current IPC uses Unix sockets (macOS/Linux only). Named pipes would be needed
for native Windows support.

## Options
1. **MVP = macOS/Linux only** — ship Unix sockets, defer Windows to post-MVP
2. **Cross-platform IPC now** — implement named pipes for Windows alongside Unix sockets
3. **Abstract IPC layer** — create transport interface, implement Unix socket first, add named pipe later

## Considerations
- MVP scope: get to 50 installs first, most Claude Code users are macOS/Linux
- Named pipe implementation adds complexity and testing burden
- WSL users can use Unix sockets (Linux layer)
- Windows native support may not be needed until Month 2+

## References
- CLAUDE.md: TODOs → "Decide IPC: Unix socket vs named pipe (Windows compat)"
EOF
)"

# Issue 11: Fail-open validation
gh issue create --repo "$REPO" \
  --title "Validate fail-open default with first 5 users" \
  --label "sev-2-medium,decision" \
  --body "$(cat <<'EOF'
## Decision Needed
MVP defaults to fail-open (allow tool calls when daemon is unreachable).
This needs validation with real users.

## Questions for Users (Week 4)
1. Is fail-open acceptable for your use case?
2. Would you need fail-closed for production/CI environments?
3. Would per-project config (`fail_behavior: open|closed` in policy.yaml) solve this?

## Risk
- Security-focused users may reject fail-open entirely
- Developer-focused users likely prefer fail-open (don't break workflow)
- Mitigation: add `fail_behavior` config in Team/Enterprise tier

## Action
- Track in Week 4 user conversation notes
- If 2+ users want fail-closed → implement `fail_behavior` config immediately
- If 0 users mention it → keep fail-open as default

## References
- CLAUDE.md: Fail Behavior section
- CLAUDE.md: Risks → "Fail-open alienates security users"
EOF
)"

echo ""
echo "=== Done ==="
echo "Created 12 labels and 11 issues."
echo "Run 'gh issue list --repo $REPO' to verify."
