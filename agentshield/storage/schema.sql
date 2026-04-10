-- AgentShield audit schema (v1, harness MVP)
--
-- tool_calls        : every intercepted PreToolUse event
-- sessions          : high-level session lifecycle bookkeeping
--
-- Harness additions (vs. pre-rethink spec):
--   source_event_id  : nullable FK to tool_calls.id; links a write back to
--                      the read event that produced its content. Populated
--                      by Month 2 provenance linking logic — Week 1 only
--                      guarantees the column + index exist.
--   provenance_tags  : JSON array of source tags (e.g. ["external",
--                      "web_fetch", "untrusted"])

CREATE TABLE IF NOT EXISTS tool_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    session_id       TEXT,
    agent_id         TEXT,
    agent_type       TEXT,
    framework        TEXT NOT NULL DEFAULT 'claude_code',
    tool             TEXT NOT NULL,
    input            TEXT,
    blocked          INTEGER NOT NULL DEFAULT 0,
    reason           TEXT,
    message          TEXT,
    duration_ms      INTEGER,
    source_event_id  INTEGER REFERENCES tool_calls(id),
    provenance_tags  TEXT
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

-- Deduplication: prevent double-logging of the same event.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup
    ON tool_calls(session_id, tool, ts, framework);

CREATE INDEX IF NOT EXISTS idx_ts        ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_session   ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_blocked   ON tool_calls(blocked);
CREATE INDEX IF NOT EXISTS idx_framework ON tool_calls(framework);
CREATE INDEX IF NOT EXISTS idx_source    ON tool_calls(source_event_id);
