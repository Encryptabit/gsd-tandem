# Phase 7: Add Reviewer Lifecycle Management to Broker - Context

**Gathered:** 2026-02-18
**Status:** Ready for planning

<domain>
## Phase Boundary

The broker gains the ability to spawn, manage, and terminate Codex reviewer instances as subprocesses. Today the broker is a passive queue — an external PowerShell script (`start-broker-reviewer.ps1`) manually launches a single Codex reviewer. This phase moves reviewer spawning inside the broker, adds auto-scaling based on backlog pressure, enforces max TTL with graceful recycling, and tracks reviewer lifecycle in the audit trail. The self-claim assignment model stays unchanged — reviewers still poll `list_reviews(wait=true)` and claim work themselves.

</domain>

<decisions>
## Implementation Decisions

### Spawning mechanism
- Spawn logic lives **inside the broker** server process (FastMCP) — no separate supervisor
- Broker spawns Codex reviewers as subprocesses, managing their lifecycle directly
- **Platform-aware spawning**: detect OS — use WSL on Windows, native spawn on Linux/macOS
- Expose MCP tools for manual override: `spawn_reviewer` and `kill_reviewer` alongside auto-scaling
- Codex spawn parameters (model, reasoning_effort, workspace_path, WSL distro) stored in **config.json**, not hardcoded
- **Config validation**: all spawn parameters must be validated before use — allowlisted model names, path existence checks, no shell metacharacter injection in command construction
- **Shell-free spawn enforcement**: all subprocess invocations must use argv-list form (never shell=True, never `bash -lc`). On WSL: `['wsl', '-d', distro, '--', 'codex', 'exec', '--sandbox', 'read-only', '--ephemeral', '--model', model, ...]` — fully shell-free argv path. On native Linux/macOS: `['codex', 'exec', ...]` directly. Prohibit any command-string assembly. Tests must verify no shell metacharacter expansion.
- **Auth guardrail for spawn/kill tools**: localhost trusted environment — same trust model as all other broker tools. spawn_reviewer enforces max pool cap, kill_reviewer only targets broker-managed reviewer IDs (cannot kill arbitrary processes). Rate-limit spawn calls to prevent runaway process creation.

### Pool scaling
- **Ratio-based scaling**: spawn a new reviewer when pending reviews exceed 3:1 ratio to active reviewers
- **Min 0, max configurable** pool bounds — pool can scale to zero when idle, cold-starts on first review
- **Idle timeout** scale-down: terminate reviewers idle longer than a configurable duration
- **Reactive + periodic** check cadence: evaluate scaling on every `create_review` AND on a background timer for scale-down checks

### Reviewer lifecycle
- **Graceful drain** on TTL expiry: mark reviewer for termination, let it finish current review, then kill
- **Session-scoped naming with unique suffix**: display names follow `codex-r1`, `codex-r2`, ... pattern (counter resets per broker session for readability), BUT internal reviewer IDs include a unique session token (e.g., `codex-r1-a7f3`) to prevent collision with stale claims from previous broker sessions. The session token is generated once at broker startup. Stale claims from dead reviewers are identified by mismatched session tokens during reclaim checks.
- **Per-reviewer stats** tracked: reviews completed, average review time, approval rate per instance
- **Full lifecycle audit**: log spawn, drain-start, terminate events in `audit_events` table

### Assignment model
- **Self-claim** stays (current model): reviewers poll and claim from pending queue — broker manages the queue
- **Claim timeout of 20 minutes**: if a reviewer claims but doesn't verdict within 20 min, reclaim and return to pending
- **Fenced reclaim**: when reclaiming a timed-out review, use a fencing token (claim generation counter) so that if the original reviewer later tries to submit a verdict, the stale claim is rejected. The reclaim operation atomically transitions the review back to pending and increments the fence, preventing duplicate verdict writes. Tests must cover the race case: reviewer A times out, review reclaimed, reviewer B claims, reviewer A tries late verdict → rejected.
- **No category routing**: all reviewers are generalists, any reviewer handles any category
- **Reviewer prompt template** stored in a **config file** (not baked into Python code), versioned separately

### Claude's Discretion
- Background timer interval for periodic scaling checks
- Exact subprocess management implementation (asyncio.subprocess vs subprocess module)
- Schema design for reviewer tracking table and fence token column
- How to detect platform (Windows vs Linux/macOS) for WSL vs native spawning
- Idle timeout default value
- Max TTL default value
- Rate-limit strategy for spawn calls (e.g., cooldown period between spawns)
- Session token generation strategy (UUID prefix, random hex, etc.)

</decisions>

<specifics>
## Specific Ideas

- Current spawn script (`scripts/start-broker-reviewer.ps1`) uses `codex exec --sandbox read-only --ephemeral` piped via WSL — preserve these Codex flags but replace shell-string piping with argv-list subprocess
- Reviewer prompt includes the full loop: list_reviews → claim → get_proposal → review → verdict → close → loop
- Prompt is passed to Codex via stdin pipe (not base64+shell), since we're using argv-list subprocess
- The diagram shows multiple proposer Claude Code sessions and multiple Codex reviewers all connecting to one broker — this is the target architecture
- On broker restart, any reviews still in "claimed" state from a previous session's reviewers should be reclaimed to pending (stale session detection)

</specifics>

<deferred>
## Deferred Ideas

- Master-reviewer orchestration layer (single verdict authority, file/symbol ownership, delegation to sub-reviewers) — future phase, captured in STATE.md pending todos
- Category-based reviewer specialization — not needed for v1 generalist pool
- Multi-reviewer consensus / quorum voting — listed in v2 requirements (EXTI-02)
- Heartbeat-based health monitoring (periodic ping from reviewer to broker) — fenced reclaim covers the timeout case for now

</deferred>

---

*Phase: 07-add-reviewer-lifecycle-management-to-broker*
*Context gathered: 2026-02-18*
