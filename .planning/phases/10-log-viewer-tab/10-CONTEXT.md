# Phase 10: Log Viewer Tab - Context

**Gathered:** 2026-02-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Browse historical JSONL log files from broker-logs/ and reviewer-logs/ directories and watch new log entries stream in real-time. The viewer renders structured log data in a readable format within the dashboard. Log file management (rotation, retention, cleanup) and log format changes are out of scope.

</domain>

<decisions>
## Implementation Decisions

### Log entry rendering
- Terminal-style monospaced display — not tables or cards
- Color-coded text by log level (ERROR red, WARN yellow, INFO default, DEBUG gray)
- Show full JSON pretty-printed for every entry (not truncated)
- Raw chronological order, no grouping by event type or reviewer session
- Each JSONL line renders as its own entry — no collapsing of consecutive reviewer_output lines

### File browser layout
- Dropdown selector at top of log viewer (not sidebar or tabs)
- Files grouped by source in dropdown: "Broker Logs" and "Reviewer Logs" as optgroup sections
- Each file shows name, file size, and last modified date (all three per SC#1)
- Auto-select most recent log file on page load — user sees content immediately

### Live tail behavior
- Selecting a log file activates live tail for that file — new entries stream automatically as they are written
- User can deactivate live tail (pause streaming) via a toggle control; reactivating resumes from current position
- Always auto-scroll to bottom when new entries appear while tail is active
- User scrolls up to read history; scrolling back down resumes auto-scroll
- Pulsing dot indicator when entries are actively streaming (calm when idle or paused)
- Load full file contents when switching files (not last-N-lines partial load)

### Text search
- Text search box filters entries by matching text in message content
- Search works during live tail — applies to existing and incoming entries
- No event type or structured field filters (text search is sufficient)

### Claude's Discretion
- Search box positioning relative to dropdown and log content
- Whether search filters (hides non-matching) or highlights (keeps all visible)
- Exact color palette for log levels (should match dashboard theme/dark mode)
- How pretty-printed JSON is formatted (indentation, line wrapping)
- SSE event design for streaming new log entries to the frontend
- Toggle control design for live tail pause/resume

</decisions>

<specifics>
## Specific Ideas

- User provided real log samples showing Codex reviewer output — entries are one-JSONL-line-per-stderr/stdout-message, meaning a single logical action (tool call + response) spans many entries
- Both stderr and stdout carry meaningful content — no distinction needed in rendering
- Log entries contain: ts, event, reviewer_id, session_token, stream, message, pid, and sometimes exit_code
- Messages can be very long (full verdict reasons, tool call JSON payloads) — show them in full

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 10-log-viewer-tab*
*Context gathered: 2026-02-26*
