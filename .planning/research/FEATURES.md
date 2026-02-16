# Feature Research

**Domain:** MCP Review Broker for AI-to-AI Code Review
**Researched:** 2026-02-16
**Confidence:** MEDIUM — Domain is novel (AI-to-AI review brokering has no established product category). Feature analysis synthesized from mature code review tools (GitHub, Gerrit, Phabricator, Reviewable) adapted to the AI agent context. Individual feature concepts are HIGH confidence; their relative prioritization for this specific use case is MEDIUM confidence.

## Feature Landscape

### Table Stakes (Users Expect These)

Features without which the broker is useless. Missing any of these means the system cannot fulfill its core promise of mediating review between two AI agents.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Review lifecycle state machine** | Without defined states (open, in_review, approved, changes_requested, closed), neither agent knows what to do next. Every review tool from GitHub to Gerrit has this. | MEDIUM | States: `pending` -> `open` -> `in_review` -> `approved` / `changes_requested` -> `closed`. Must handle re-review cycles (changes_requested -> open -> in_review). |
| **Proposal creation (intent + unified diff)** | The proposer (Claude Code) must express WHAT it wants to change and WHY. Unified diff is the universal patch exchange format. Without structured proposals, the reviewer has nothing actionable to evaluate. | MEDIUM | Proposal = intent description (natural language) + unified diff (machine-parseable). Must validate diff syntax on submission. |
| **Verdict submission (approve / request changes / comment)** | Mirrors GitHub's three review actions. The reviewer must be able to approve, block, or comment without blocking. This is the minimum viable response vocabulary. | LOW | Three verdict types: APPROVE, REQUEST_CHANGES, COMMENT. APPROVE unblocks proposer. REQUEST_CHANGES blocks until revision. COMMENT is non-blocking feedback. |
| **Agent identity tracking** | Both agents need to know WHO proposed what and WHO reviewed it. Critical for observability and audit trail. PROJECT.md explicitly requires "full agent identity." | LOW | Identity includes: agent_type (claude-code, codex, human), agent_role (proposer, reviewer), and GSD context (phase, plan, task). Stored on every message. |
| **Blocking wait mechanism** | Claude Code must pause execution until the reviewer responds. Without this, the proposer applies changes before review, defeating the purpose. PROJECT.md specifies "blocking default." | MEDIUM | Proposer polls or subscribes to review. Must handle timeouts gracefully. Default is blocking; optimistic mode is a differentiator. |
| **Message exchange (back-and-forth)** | Reviewer may request changes; proposer must respond with revised diff; reviewer re-evaluates. A single round-trip is insufficient. GitHub, Gerrit, and Phabricator all support multi-round review. | MEDIUM | Messages form a thread on the review. Each message has: sender identity, content (text and/or diff), timestamp, message_type (comment, revision, verdict). |
| **Review creation and claiming** | Proposer creates review; reviewer claims it. Without this, reviews sit in limbo. Gerrit's "assign reviewer" and GitHub's "request review" are table stakes. | LOW | Create returns review_id. Claim marks reviewer identity. Only one reviewer per review (v1 simplification). |
| **Diff transport in unified format** | Unified diff is the lingua franca. Both `git apply` and human readers understand it. Anything else adds unnecessary translation. | LOW | Standard unified diff with context lines. Must support multi-file diffs. Store as text blob, not parsed AST. |
| **SQLite persistence** | Reviews must survive process restarts. Both agents may disconnect and reconnect. PROJECT.md specifies SQLite under .planning/. | LOW | Single SQLite file. Tables: reviews, messages, verdicts. Simple schema, no ORM needed. |
| **MCP tool interface** | Both agents interact via MCP tool calls. This is the transport layer. Without MCP tools, agents cannot reach the broker. PROJECT.md specifies FastMCP with Streamable HTTP. | MEDIUM | Tools: `create_review`, `claim_review`, `add_message`, `submit_verdict`, `get_review`, `list_reviews`, `get_messages`. Each tool = one MCP endpoint. |

### Differentiators (Competitive Advantage)

Features that elevate the broker beyond bare-minimum functionality. Not required for v1 launch, but valuable when added.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Counter-patch submission by reviewer** | Reviewer doesn't just say "fix this" — they supply an alternative unified diff. Eliminates the "vague rejection" problem. Unique to AI-to-AI review where both parties can write code. | MEDIUM | Reviewer attaches a diff to their REQUEST_CHANGES or COMMENT message. Proposer can accept, reject, or merge the counter-patch. Requires diff validation on reviewer side too. |
| **Optimistic execution mode** | Proposer applies changes immediately but marks them provisional. If reviewer rejects, changes are rolled back. Speeds up the pipeline for low-risk changes. | HIGH | Requires git stash/branch management for rollback. Risk of partial application. Must be opt-in per review or per config. PROJECT.md lists as "optional." |
| **Configurable review granularity** | Per-task (default) vs per-plan review. Per-plan batches multiple tasks into one review, reducing round-trips for experienced reviewers. | LOW | Config flag in .planning/config.json. Per-plan mode groups all task diffs into one review proposal. |
| **Review priority / urgency levels** | Some proposals are blocking critical path; others are low-risk. Priority lets the reviewer triage. Analogous to Gerrit's label weighting. | LOW | Priority enum: critical, normal, low. Affects ordering in reviewer's queue. No enforcement in v1, purely advisory. |
| **Structured inline comments (line-specific)** | Reviewer can comment on specific diff hunks rather than the whole proposal. Mirrors GitHub's line-level PR comments. More precise feedback. | HIGH | Requires parsing unified diff into hunks, mapping comments to line ranges. Significant complexity for AI-to-AI where natural language "line 42 has a bug" may suffice. |
| **Review metrics and analytics** | Track approval rate, average review time, rejection reasons, round-trip count. Enables workflow optimization. | MEDIUM | Computed from existing data (timestamps, verdict counts). Expose via MCP tool or CLI report. Useful for tuning agent prompts. |
| **Automatic conflict detection** | Before submitting a proposal, check if the diff applies cleanly to the current working tree. Flag conflicts proactively. Analogous to `git apply --check`. | MEDIUM | Run `git apply --check` on the diff. Report conflicts as structured data. Prevents wasted review cycles on unapplicable diffs. |
| **Review templates / checklists** | Reviewer gets a structured checklist (security, correctness, style) to evaluate against. Ensures consistent review quality across different reviewer agents. | LOW | Template is a markdown checklist included in the review creation message. Different templates per review granularity or phase type. |
| **Notification/callback mechanism** | Push notification when a review needs attention rather than polling. Reduces latency and resource waste. | MEDIUM | WebSocket or SSE from broker to connected agents. Falls back to polling if not supported. FastMCP Streamable HTTP may support this natively. |
| **Review delegation / escalation** | If the primary reviewer is unavailable or uncertain, escalate to a different reviewer (e.g., from Codex to human). Gerrit supports this with reviewer groups. | MEDIUM | Add a second reviewer, transfer claim, or escalate to human. Requires multi-reviewer state tracking. |
| **Diff application assistance** | Broker can apply approved diffs via `git apply` on behalf of the proposer, reducing the proposer agent's responsibility. | MEDIUM | Security concern: broker executing git commands. May conflict with "broker never executes shell commands" constraint in PROJECT.md. Consider as proposer-side helper instead. |
| **Review history / audit log** | Complete history of all reviews, verdicts, and messages across the project lifetime. Enables post-mortem analysis and process improvement. | LOW | Already exists implicitly in SQLite. This feature is about exposing it — a `list_reviews` with filtering, a summary report tool. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem appealing but should be deliberately excluded. Building these would add complexity without proportional value, or actively harm the system.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Rich diff rendering (syntax highlighting, side-by-side view)** | Looks professional, familiar from GitHub UI | Agents don't need visual rendering — they read text. Adds a web UI dependency that contradicts the local-MCP-only design. Humans can read unified diff or use `git diff`. | Provide raw unified diff. Humans who want rendering can paste into any diff viewer. |
| **Multi-reviewer consensus (quorum voting)** | Gerrit has +2/-2 voting with multiple reviewers | Massively increases state complexity for v1. AI-to-AI review is 1:1 by design. Multi-reviewer creates deadlock risk and ambiguous "who wins" scenarios. | Single reviewer per review. If consensus needed, run sequential reviews with different reviewers. |
| **Authentication / authorization** | Security best practice | PROJECT.md explicitly scopes to localhost trusted environment. Auth adds token management, session handling, and failure modes. Zero value on 127.0.0.1. | Rely on localhost binding. Document that broker must never be exposed to network. |
| **File-level access control (CODEOWNERS equivalent)** | Route reviews to specialists based on file paths | Over-engineering for a 1:1 review broker. There's one reviewer. Routing logic assumes multiple reviewers exist. | Single reviewer reviews everything. If specialization needed, configure different reviewer per phase in GSD config. |
| **Automatic merge / commit on approval** | Streamline the happy path | Violates "Claude remains the sole writer/committer" constraint. Broker should mediate, not act. The proposer agent applies its own diffs after approval. | Return approval verdict to proposer. Proposer applies diff and commits via its own git tools. |
| **Real-time collaborative editing** | Google Docs-style co-editing of diffs | Both agents operate asynchronously via MCP tool calls. Real-time collaboration requires WebSocket sync, OT/CRDT, and a fundamentally different architecture. | Asynchronous message exchange with counter-patches. Agents take turns, they don't edit simultaneously. |
| **Natural language diff generation** | Describe changes in English, let broker generate diff | Unreliable — LLM-generated diffs have syntax errors, context mismatches, off-by-one line numbers. The proposer agent (Claude Code) already has `git diff` access. | Require proposer to submit machine-generated unified diff from `git diff`. Natural language goes in the intent field. |
| **Review bot / linting integration** | Run eslint, type-check, tests as part of review | Broker is a communication channel, not a CI system. Adding execution makes the broker a security risk and creates a dependency on project-specific tooling. | Proposer runs tests before submitting. Reviewer can request "run tests" as a review comment. Each agent runs tools in its own sandbox. |
| **Persistent WebSocket connections** | Lower latency than HTTP polling | Adds connection management complexity (reconnection, heartbeat, state sync). Streamable HTTP already supports server-sent events. MCP Streamable HTTP transport handles this. | Use MCP Streamable HTTP transport which supports both request-response and streaming. Poll with backoff as fallback. |
| **Diff semantic analysis (AST-level)** | Understand what changed at a structural level, not just text | Requires language-specific parsers for every language in the project. Enormous scope. AI agents already understand code semantics from reading the diff text. | Let the AI reviewer interpret the diff semantically. The broker transports diffs, it does not analyze them. |

## Feature Dependencies

```
[MCP Tool Interface]
    |
    +--requires--> [SQLite Persistence]
    |
    +--requires--> [Review Lifecycle State Machine]
    |                   |
    |                   +--requires--> [Verdict Submission]
    |                   |
    |                   +--requires--> [Proposal Creation]
    |                   |                   |
    |                   |                   +--requires--> [Diff Transport (unified format)]
    |                   |                   |
    |                   |                   +--requires--> [Agent Identity Tracking]
    |                   |
    |                   +--requires--> [Review Creation and Claiming]
    |                   |
    |                   +--enhances--> [Message Exchange (back-and-forth)]
    |                                       |
    |                                       +--enhances--> [Counter-patch Submission]
    |
    +--requires--> [Blocking Wait Mechanism]

[Configurable Review Granularity] --enhances--> [Proposal Creation]

[Optimistic Execution Mode] --conflicts--> [Blocking Wait Mechanism]
    (must be alternative, not concurrent)

[Review Priority] --enhances--> [Review Creation and Claiming]

[Automatic Conflict Detection] --enhances--> [Proposal Creation]

[Review Metrics] --requires--> [SQLite Persistence]
                 --requires--> [Review Lifecycle State Machine]

[Notification Mechanism] --enhances--> [Blocking Wait Mechanism]
    (replaces polling with push)

[Review Delegation] --requires--> [Review Creation and Claiming]
                    --conflicts--> single-reviewer simplification in v1
```

### Dependency Notes

- **MCP Tool Interface requires SQLite Persistence:** Tools need somewhere to store and retrieve review state. Without persistence, no tool call produces durable results.
- **Review Lifecycle State Machine requires Verdict Submission, Proposal Creation, and Review Creation:** The state machine cannot transition without these three operations being defined.
- **Proposal Creation requires Diff Transport and Agent Identity:** A proposal without a diff is empty; a proposal without identity is anonymous and untrackable.
- **Message Exchange enhances State Machine:** The state machine can function with single-round approve/reject. Message exchange enables multi-round review cycles.
- **Counter-patch Submission enhances Message Exchange:** Counter-patches are a special type of message. Without the messaging substrate, counter-patches have nowhere to attach.
- **Optimistic Execution Mode conflicts with Blocking Wait:** These are alternative strategies. The system uses one or the other per review, never both simultaneously.
- **Notification Mechanism enhances Blocking Wait:** Push notifications replace polling inside the blocking wait, reducing latency. Not required — polling works.
- **Review Delegation conflicts with v1 single-reviewer model:** Delegation requires multi-reviewer state tracking, which is explicitly deferred.

## MVP Definition

### Launch With (v1)

Minimum viable product — what is needed to demonstrate Claude Code proposing changes and a reviewer approving/rejecting them through the broker.

- [ ] **MCP Tool Interface** — Without MCP tools, agents cannot interact with the broker at all
- [ ] **SQLite Persistence** — Reviews must survive across tool calls and agent restarts
- [ ] **Review Lifecycle State Machine** — Defined states and valid transitions so both agents agree on review status
- [ ] **Proposal Creation (intent + unified diff)** — Proposer must express what and why
- [ ] **Verdict Submission (approve / request changes / comment)** — Reviewer must respond with a structured decision
- [ ] **Agent Identity Tracking** — Every message attributed to a specific agent with GSD context
- [ ] **Review Creation and Claiming** — Proposer opens, reviewer claims
- [ ] **Blocking Wait Mechanism** — Proposer pauses until reviewer responds
- [ ] **Message Exchange (back-and-forth)** — At least one revision cycle (request changes -> revise -> re-review)
- [ ] **Diff Transport (unified format)** — Standard format, validated on submission

### Add After Validation (v1.x)

Features to add once the core review loop is proven to work end-to-end.

- [ ] **Counter-patch Submission** — Add when reviewers demonstrate they want to supply fixes, not just flag problems
- [ ] **Configurable Review Granularity** — Add when users request per-plan batching to reduce review overhead
- [ ] **Automatic Conflict Detection** — Add when stale diffs cause wasted review cycles
- [ ] **Review Priority** — Add when review queues grow and triage becomes necessary
- [ ] **Notification Mechanism** — Add when polling latency becomes a bottleneck
- [ ] **Review History / Audit Log** — Add when users want post-mortem analysis (data already exists in SQLite)

### Future Consideration (v2+)

Features to defer until the core broker is battle-tested.

- [ ] **Optimistic Execution Mode** — Requires git rollback infrastructure; defer until blocking mode proves too slow
- [ ] **Structured Inline Comments** — Defer until natural-language hunk references prove insufficient for AI reviewers
- [ ] **Review Delegation / Escalation** — Defer until multi-reviewer scenarios are needed
- [ ] **Review Metrics and Analytics** — Defer until enough review data exists to make metrics meaningful
- [ ] **Review Templates / Checklists** — Defer until review quality variance is observed across reviewer types

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| MCP Tool Interface | HIGH | MEDIUM | P1 |
| SQLite Persistence | HIGH | LOW | P1 |
| Review Lifecycle State Machine | HIGH | MEDIUM | P1 |
| Proposal Creation | HIGH | MEDIUM | P1 |
| Verdict Submission | HIGH | LOW | P1 |
| Agent Identity Tracking | HIGH | LOW | P1 |
| Review Creation and Claiming | HIGH | LOW | P1 |
| Blocking Wait Mechanism | HIGH | MEDIUM | P1 |
| Message Exchange | HIGH | MEDIUM | P1 |
| Diff Transport | HIGH | LOW | P1 |
| Counter-patch Submission | MEDIUM | MEDIUM | P2 |
| Configurable Review Granularity | MEDIUM | LOW | P2 |
| Automatic Conflict Detection | MEDIUM | MEDIUM | P2 |
| Review Priority | LOW | LOW | P2 |
| Notification Mechanism | MEDIUM | MEDIUM | P2 |
| Review History / Audit Log | LOW | LOW | P2 |
| Optimistic Execution Mode | MEDIUM | HIGH | P3 |
| Structured Inline Comments | LOW | HIGH | P3 |
| Review Delegation | MEDIUM | MEDIUM | P3 |
| Review Metrics | LOW | MEDIUM | P3 |
| Review Templates | LOW | LOW | P3 |

**Priority key:**
- P1: Must have for launch — broker is non-functional without these
- P2: Should have, add when possible — improves quality and efficiency
- P3: Nice to have, future consideration — adds polish but not core value

## Competitor Feature Analysis

| Feature | GitHub PR Reviews | Gerrit | Phabricator Differential | Reviewable | Our Approach (MCP Broker) |
|---------|-------------------|--------|--------------------------|------------|---------------------------|
| Review states | PENDING, COMMENT, APPROVED, CHANGES_REQUESTED, DISMISSED | Custom labels with +2/+1/0/-1/-2 voting | Needs Review, Accepted, Needs Revision, Closed | Per-discussion dispositions (Discussing, Blocking, Working, Satisfied) | Simplified: pending, open, in_review, approved, changes_requested, closed. No voting — binary verdict. |
| Diff format | GitHub-rendered diff with line comments | Gerrit-rendered diff with inline comments | Phabricator-rendered diff with inline comments | Multi-revision diff matrix | Raw unified diff text. Agents parse it themselves. No rendering needed. |
| Threading model | PR-level comments + line-level threads + review body | Change-level comments + inline comments + patchset-scoped | Revision-level + inline comments | Discussion threads with dispositions and resolution tracking | Flat message thread per review. Messages can contain text, diffs, or verdicts. Simple and sufficient for AI-to-AI. |
| Multi-reviewer | Yes (CODEOWNERS, required reviewers) | Yes (label-based voting, multiple reviewers) | Yes (reviewer groups, blocking reviewers) | Yes (per-file reviewer tracking) | No. Single reviewer per review. Simplifies state machine drastically. |
| Counter-patches | Suggested changes (single-line only) | Amend and push new patchset | Update revision | New revision via push | Full unified diff counter-patches from reviewer. More powerful than GitHub's suggestions. |
| Resolution tracking | Resolve/unresolve conversation threads | Submit when all labels satisfied | Accept/reject revision | Disposition-based resolution (all Satisfied, none Blocking) | Verdict-based: APPROVE resolves, REQUEST_CHANGES reopens. No per-thread resolution needed. |
| Audit trail | Timeline events API | Change log with all patchsets | Revision history + audit log | Full comment history with dispositions | SQLite message log with timestamps and agent identity. Complete audit trail by construction. |
| Identity model | GitHub user accounts | Gerrit user accounts + service accounts | Phabricator user accounts | GitHub user accounts | Agent identity: type, role, phase, plan, task. Richer than user accounts for GSD context. |
| Wait/blocking | Async (humans check when ready) | Async with email notifications | Async with notification | Async with status tracking | Blocking by default (agent pauses). Unique to AI-to-AI where blocking is viable and desirable. |
| Conflict detection | Merge conflict detection at branch level | Patchset conflict detection on submit | Pre-commit hooks | GitHub-delegated | `git apply --check` on proposal submission. Proactive, before review begins. |

## Sources

- [GitHub REST API — Pull Request Reviews](https://docs.github.com/en/rest/pulls/reviews) — HIGH confidence. Official API documentation for GitHub's review data model, states, and endpoints.
- [GitHub — About Pull Request Reviews](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/about-pull-request-reviews) — HIGH confidence. Official documentation for review workflow and states.
- [Gerrit — Review Labels](https://gerrit.wikimedia.org/r/Documentation/config-labels.html) — HIGH confidence. Official Gerrit docs on label-based voting system.
- [Gerrit — Quick Introduction](https://gerrit.cloudera.org/Documentation/intro-quick.html) — HIGH confidence. Official Gerrit workflow documentation.
- [Phabricator — Differential User Guide](https://secure.phabricator.com/book/phabricator/article/differential/) — HIGH confidence. Official Phabricator documentation for revision workflow.
- [Reviewable — Discussions](https://docs.reviewable.io/discussions.html) — HIGH confidence. Official Reviewable documentation for threading/discussion model.
- [Reviewable — Reviews](https://docs.reviewable.io/reviews) — HIGH confidence. Official Reviewable documentation for review state tracking.
- [GNU Diffutils — Unified Format](https://www.gnu.org/software/diffutils/manual/html_node/Unified-Format.html) — HIGH confidence. Canonical specification of unified diff format.
- [Git — git-apply Documentation](https://git-scm.com/docs/git-apply) — HIGH confidence. Official Git documentation for patch application and conflict detection.
- [Graphite — Gerrit's Approach to Code Review](https://graphite.com/guides/gerrits-approach-to-code-review) — MEDIUM confidence. Third-party guide synthesizing Gerrit concepts.
- [Graphite — Differential: Phabricator's Code Review Application](https://graphite.dev/guides/differential-phabricators-code-review-application) — MEDIUM confidence. Third-party guide synthesizing Phabricator concepts.
- [State Machine for Approval Process](https://medium.com/@wacsk19921002/simplifying-approval-process-with-state-machine-a-practical-guide-part-1-modeling-26d8999002b0) — MEDIUM confidence. Practical guide to modeling approval workflows as state machines.
- [AI Agent Protocols 2026](https://www.ruh.ai/blogs/ai-agent-protocols-2026-complete-guide) — LOW confidence. Overview of A2A and MCP protocol landscape, general patterns.

---
*Feature research for: MCP Review Broker (AI-to-AI Code Review)*
*Researched: 2026-02-16*
