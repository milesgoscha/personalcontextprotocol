# Personal Context Protocol (PCP) — Unified Specification v0.1

## 0. Philosophy & Motivation

### Core Thesis

**"Personal context needs structure to be portable."**

Without structure, personal context is messy data trapped wherever it was created. With structure, it becomes something that can travel—across agents, products, and time.

### The Problem: Context is Siloed

Every AI tool starts from zero about you:
- Claude remembers your chats (but only in Claude)
- Cursor learns your style (but only in Cursor)
- Your calendar knows your schedule (but agents can't access it uniformly)

As agents proliferate, this problem worsens. You either:
1. Re-teach every agent about yourself
2. Lock into one ecosystem
3. Accept fragmented, inconsistent AI experiences

### Three Types of Context

| Type | Description | Lifespan | Location |
|------|-------------|----------|----------|
| **Inference Context** | Conversation, reasoning traces, working memory | Ephemeral (dies with session) | Inside model's context window |
| **Work Context** | Codebases, documents, APIs, tools | Task-specific | Repos, databases, MCP servers |
| **Personal Context** | Who you are, your patterns, preferences, history | Persistent, portable | ??? (currently: siloed everywhere) |

**The key insight:** Personal context is categorically different—it crosses boundaries between sessions, tools, and time. It belongs to YOU, not to any task or product.

### Relationship to Existing Protocols

| Layer | Protocol/Pattern | Purpose |
|-------|------------------|---------|
| Agent Orchestration | OpenProse, LangGraph | How agents coordinate |
| Tool/Resource Access | MCP | How agents use tools |
| **Personal Context** | **PCP** | How agents know YOU |

PCP is a **profile on MCP**—like REST is a pattern on HTTP. Any agent that speaks PCP can access any PCP-compliant context source.

---

## 1. Purpose

PCP defines an application-layer protocol for representing and exchanging a person's contextual state (identity, events, learnings, reflections) with AI agents. It is transport-agnostic but intentionally aligns with the Model Context Protocol (MCP) so that PCP capabilities can be exposed as MCP tools.

PCP targets agentic systems such as Recursive Language Models (RLMs) where prompts are treated as external environments; PCP nodes supply that environment via structured, queryable context instead of brittle prompt stuffing.

---

## 2. Terminology

- **PCP Node**: Server or daemon that stores personal context and exposes the PCP interface.
- **Collector**: Component that emits observations (events) into a PCP node via `observe`.
- **Agent**: Consumer that issues `query/learn/reflect` operations. Agents may also be RLMs running the user's workloads.
- **Object**: Typed context record (`identity`, `event`, `learning`, `reflection`) wrapped in a versioned envelope.
- **Disclosure level**: Summary/detail/raw tiers describing how much of an object is revealed.
- **Scope token**: Auth credential that limits which operations/object types an agent may access.

---

## 3. Design Principles

### 1. User Ownership
Data lives in user-controlled nodes. Tokens are explicitly granted, auditable, and revocable. Nodes MUST support export/import of their complete history.

### 2. Private by Default
Personal context flows FROM you—it is yours by definition. The default state is private. Sharing is an explicit act, not the absence of protection.

**Specific mechanisms:**
- Data stored locally in encrypted store by default
- Remote sync requires explicit grants
- Tokens handed to agents are scoped (time-bounded + object type)
- Audit logs for all access
- Redaction layers before sharing with cloud agents

### 3. Collection Agnostic
PCP does not prescribe how data is collected; it standardizes how collected data is described, retained, and shared. Collectors register via capabilities describing the event types they emit.

### 4. RLM-Native
Objects are externally addressable via `pcp://` URIs with byte offsets and progressive disclosure so recursive agents can peek, filter, and fetch just-in-time. Agents call `query()` in reasoning loops, not in prompt construction.

### 5. Progressive Disclosure
Every object is retrievable at `summary`, `detail`, or `raw` levels with lineage metadata so agents can inspect minimally first and request more when necessary.

### 6. Interoperability-First
Schemas include explicit versioning, namespaced metadata, and capability negotiation so heterogeneous collectors and agents remain compatible over time.

---

## 4. Architecture Overview

```
Collectors  --observe-->  PCP Node  <--query/learn/reflect-- Agents / RLM orchestrators
                               ^
                               |
                          Audit / Policy
```

- A PCP node MAY expose its interface via HTTPS, MCP, or both
- The canonical identifier for a user's node is `pcp://<identity>`
- Transports resolve this to `https://host/.well-known/pcp` or to an MCP provider descriptor
- Nodes MUST publish a `describe` manifest listing supported transports, schema versions, extensions, and disclosure limits

### The pcp://me Vision

The goal: **any agent can point to `pcp://me` and instantly reason over your world.**

```
pcp://me                    → Your personal context endpoint
pcp://me/identity           → Stable facts about you
pcp://me/events             → Your event stream
pcp://me/events/{date}      → Events for a specific date
pcp://me/learnings          → What's known about you
pcp://me/learnings/{cat}    → Learnings by category
pcp://me/reflections        → Synthesized summaries
pcp://me/reflections/latest → Most recent reflection
```

This endpoint could live locally (`localhost:6001/pcp`), on your home server, or in the cloud with encryption + explicit grants. The key: it's **agent-agnostic and always addressable**.

---

## 5. Data Model

All objects share a common envelope before a type-specific payload.

### 5.1 Common Envelope

Every PCP object is wrapped in a versioned envelope:

```json
{
  "id": "pcp://milesgoscha/evt/9f1d...",
  "type": "identity" | "event" | "learning" | "reflection",
  "version": "0.1.0",
  "schema": "pcp.event.v1",
  "created_at": "2026-01-04T17:21:05Z",
  "updated_at": "2026-01-04T17:21:05Z",
  "subject": {"id": "did:key:z6Mk...", "display_name": "Miles"},
  "tags": ["work", "browser"],
  "visibility": {
    "classification": "private" | "shared" | "public",
    "allowed_scopes": ["agent:research", "agent:planner"]
  },
  "disclosure": {
    "available_levels": ["summary", "detail", "raw"],
    "default_level": "summary"
  },
  "lineage": {
    "parents": ["pcp://..."],
    "sources": ["collector:browser@1.0"],
    "confidence": 0.97
  },
  "attachments": [
    {
      "name": "screencap",
      "mime": "image/png",
      "size_bytes": 204800,
      "uri": "pcp://milesgoscha/blob/att-123",
      "hash": "sha256-..."
    }
  ],
  "extensions": {"custom.namespace/key": {"foo": "bar"}}
}
```

### 5.2 Identity Payload

Identity represents stable, near-immutable facts about the user. Most of "who you are" is learnings, not identity.

```json
{
  "name": "Miles Goscha",
  "timezone": "America/Los_Angeles",
  "locale": "en-US",
  "did": "did:key:z6Mk...",
  "summary": "Miles, PST timezone, English locale",
  "detail": {
    "preferred_name": "Miles",
    "pronouns": "he/him"
  },
  "custom": {
    "com.company.pcp/role": "engineer"
  }
}
```

Identity is queryable via `pcp://me/identity` and through `pcp/query { type: "identity" }`.

### 5.3 Event Payload

Events capture atomic observations. `detail` MAY be partially redacted at summary level.

```json
{
  "event_kind": "application.navigation",
  "timestamp": "2026-01-04T17:20:58Z",
  "actor": "pcp://milesgoscha/device/macbook-pro",
  "summary": "Visited arxiv.org RLM whitepaper",
  "detail": {
    "application": "Arc",
    "window_title": "Recursive Language Models",
    "url": "https://arxiv.org/abs/2512.24601",
    "input_sample": "opened pdf"
  },
  "raw_ref": {
    "uri": "pcp://milesgoscha/blob/raw-evt-9f1d",
    "encoding": "binary",
    "offsets": [{"start": 0, "length": 4096}]
  }
}
```

### 5.4 Learning Payload

Learnings are durable, queryable facts that agents can rely on until revoked.

```json
{
  "key": "preferred_workflow",
  "statement": "Prefers progressive disclosure when reviewing research",
  "confidence": 0.82,
  "category": "preferences",
  "derived_from": ["pcp://.../evt/9f1d"],
  "valid_for": {
    "start": "2026-01-01T00:00:00Z",
    "end": null
  },
  "summary": "User defaults to summary-first workflows",
  "detail": {
    "evidence_snippets": ["Email thread 2025-12-18", "RLM study notes"],
    "supporting_metrics": {"count": 14}
  }
}
```

### 5.5 Reflection Payload

Reflections are episodic or situational snapshots synthesized from events/learning trajectories.

```json
{
  "scope": "weekly",
  "horizon": {
    "start": "2025-12-28",
    "end": "2026-01-04"
  },
  "summary": "Focused on RLM research & PCP design",
  "content": "This week was primarily devoted to...",
  "detail": {
    "themes": [
      {"label": "agentic protocols", "salience": 0.9},
      {"label": "privacy", "salience": 0.7}
    ],
    "open_questions": ["Define schema rigidity vs flexibility"]
  },
  "sources": ["pcp://.../evt/...", "pcp://.../lrn/..."],
  "raw_ref": null
}
```

---

## 6. Operations

All operations are idempotent unless noted. Payloads are JSON; transports MAY provide binary attachments separately. PCP nodes MUST implement `describe`, `query`, `observe`, `learn`, and `reflect`; `subscribe` is optional but recommended.

### 6.1 `describe`

Purpose: capability discovery.

**Request:** `{}`

**Response:**
```json
{
  "node_id": "pcp://milesgoscha",
  "schema_versions": {
    "pcp.identity": "1.0",
    "pcp.event": "1.0",
    "pcp.learning": "1.0",
    "pcp.reflection": "0.9"
  },
  "transports": [
    {"type": "https", "endpoint": "https://pcp.milesgoscha.com/api"},
    {"type": "mcp", "endpoint": "mcp://pcp-provider"}
  ],
  "auth": {
    "supported": ["oauth2", "mutual_tls"],
    "scopes": ["query:event.summary", "query:learning.detail", "observe:event"]
  },
  "limits": {
    "max_query_items": 500,
    "max_attachment": 10485760
  }
}
```

### 6.2 `query`

Supports progressive disclosure so RLMs can iterate over large corpora.

**Request fields:**
- `object_types`: subset of `identity|event|learning|reflection`
- `filter`: declarative structure specifying predicates
- `disclosure`: requested level (`summary` default)
- `page`: cursor-based pagination (cursor token + limit) or streaming
- `sort`: e.g., `{"field": "created_at", "direction": "desc"}`
- `summarize`: boolean, if true returns LLM-generated summary instead of items

**Filter schema example:**
```json
{
  "object_types": ["event"],
  "filter": {
    "time": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-04T23:59:59Z"},
    "tags": ["rlm"],
    "predicates": [
      {"path": "detail.application", "op": "eq", "value": "Arc"},
      {"path": "summary", "op": "matches", "value": "Recursive"}
    ]
  },
  "disclosure": "summary",
  "page": {"size": 50}
}
```

**Response:**
```json
{
  "items": [
    {
      "envelope": { ... },
      "payload": { "summary": "Visited arxiv.org RLM whitepaper" },
      "disclosure": "summary",
      "detail_available": true,
      "raw_available": true
    }
  ],
  "next_page": {
    "cursor": "g2wAAAAB...",
    "remaining_estimate": 1200
  }
}
```

**If `summarize: true`:**
```json
{
  "summary": "Today you focused primarily on PCP protocol design...",
  "sources": ["pcp://.../evt/123", "pcp://.../evt/456"]
}
```

### 6.3 `observe`

Collectors append one or more events.

**Request:**
```json
{
  "objects": [
    {
      "envelope": {"type": "event", "tags": ["keyboard"], ...},
      "payload": {"event_kind": "input.keystroke", ...}
    }
  ],
  "ingest_mode": "append" | "replace",
  "dedupe_keys": ["collector_id", "collector_event_id"]
}
```

**Response:** Returns canonical IDs plus any redactions.

### 6.4 `learn`

Agents or background jobs submit or update learnings. Nodes MUST record lineage referencing supporting events.

**Request:**
```json
{
  "key": "preferred_workflow",
  "statement": "Prefers summary-first workflows",
  "confidence": 0.82,
  "category": "preferences",
  "derived_from": ["pcp://...evt..."],
  "upsert": true
}
```

**Response:** Includes resulting object with `previous` value if updated.

### 6.5 `reflect`

Similar to `learn` but optimized for episodic summaries. Supports `replace_scope` semantics where a reflection for the same scope/horizon is replaced atomically.

**Request:**
```json
{
  "prompt": "What did I work on this week?",
  "scope": "weekly",
  "horizon": {"start": "2025-12-28", "end": "2026-01-04"},
  "context": ["events", "learnings"],
  "save": true,
  "replace_scope": true
}
```

### 6.6 `subscribe` (optional)

Long-lived stream delivering new objects or mutations.

**Request:**
```json
{
  "object_types": ["learning", "reflection"],
  "minimum_disclosure": "summary",
  "since": "cursor"
}
```

Transport MAY be Server-Sent Events, WebSocket, or MCP streaming responses.

---

## 7. MCP Tool Signatures

For agents using MCP, PCP operations map to these tool signatures:

### pcp/query
```yaml
Parameters:
  type:       enum [identity, events, learnings, reflections]
  filter:     object?      # type-specific filtering (see §6.2)
  timerange:  object?      # {after: datetime?, before: datetime?}
  limit:      integer?     # max results (default: 100)
  disclosure: enum [summary, detail, raw]  # default: summary
  summarize:  boolean?     # return LLM summary vs raw data

Returns:
  items:      array        # of objects at requested disclosure level
  count:      integer
  next_page:  object?      # cursor for pagination
```

### pcp/observe
```yaml
Parameters:
  event_kind: string       # "app_switch", "keystroke_burst", etc.
  data:       object       # flexible payload
  source:     string       # collector identifier
  timestamp:  datetime?    # defaults to now
  tags:       array?       # classification tags

Returns:
  id:         string       # canonical pcp:// URI
  timestamp:  datetime
```

### pcp/learn
```yaml
Parameters:
  key:        string       # "preferred_ide", "working_hours"
  statement:  string       # human-readable fact
  confidence: float?       # 0.0-1.0 (default: 1.0)
  category:   string?      # "preferences", "patterns", "facts"
  derived_from: array?     # source event/learning IDs

Returns:
  id:         string
  previous:   object?      # previous value if updated
```

### pcp/reflect
```yaml
Parameters:
  prompt:     string       # what to reflect on
  scope:      string?      # "daily", "weekly", custom
  horizon:    object?      # {start: date, end: date}
  context:    array?       # ["events", "learnings"] to include
  save:       boolean?     # persist as Reflection (default: false)

Returns:
  content:    string       # the reflection
  sources:    array        # IDs that informed it
  id:         string?      # if saved
```

### pcp/describe
```yaml
Parameters: {}

Returns:
  node_id:         string
  schema_versions: object
  transports:      array
  auth:            object
  limits:          object
```

---

## 8. Progressive Disclosure Rules

- Nodes MUST store `summary`, `detail`, and optionally `raw` or `raw_ref` for each object
- `summary` is <=1KB UTF-8 text optimized for fast scanning
- `detail` may include structured JSON; nodes MAY redact fields based on token scopes
- `raw` references are handles to binary/log payloads. Agents fetch them via signed URLs or blob APIs
- Every response includes `detail_available` and `raw_available` booleans so agents know when to recurse, mirroring RLM peek-before-drill behavior

---

## 9. Security & Privacy

### Identity Anchoring
Each node is anchored to a DID or public key published in its `describe` manifest. This enables verification across nodes and transports.

### Authentication & Scopes
Tokens enumerate scopes like `query:event.summary` or `learn:write`. Scopes may limit disclosure levels (e.g., `summary-only`).

**Example scopes:**
- `query:identity` - read identity facts
- `query:event.summary` - read event summaries only
- `query:event.detail` - read event details
- `query:learning.*` - read all learning disclosure levels
- `observe:event` - write events
- `learn:write` - create/update learnings
- `reflect:write` - create reflections

### Audit Requirements
Nodes MUST append immutable audit events referencing requester identity, operation, filters, and disclosure level. Audit entries are themselves PCP events under a reserved namespace (`pcp.audit.*`).

### Redaction Policies
Nodes may define redaction transforms per scope. Agents MUST handle `redacted_fields` metadata in responses gracefully.

### Privacy UX Checklist
- [ ] Data stored locally in encrypted store by default
- [ ] Remote sync requires explicit user grant
- [ ] Tokens are time-bounded + object-type scoped
- [ ] All access logged to audit trail
- [ ] Redaction layers applied before sharing with cloud agents
- [ ] User can revoke any token at any time
- [ ] Export/import of complete history supported

---

## 10. Versioning & Extensions

### Schema Versioning
Schemas use semantic versions (major.minor.patch). Backwards-compatible additions bump minor; breaking changes bump major.

### Extension Namespacing
`extensions` map allows namespaced data: keys MUST be reverse-DNS (e.g., `com.miles.pcp.ritual`).

### Capability Negotiation
Clients call `describe` and MUST verify the node supports required schema versions before using advanced fields.

---

## 11. Example Agent Flow (RLM-aligned)

1. Agent resolves `pcp://milesgoscha` via `.well-known/pcp` and authenticates
2. Calls `describe` to learn that `event.summary` access is allowed at 1,000 items/min
3. Issues `query` for `event` objects tagged `rlm` at `summary` disclosure. Receives lightweight rows
4. For high-salience rows, agent re-queries by `id` requesting `detail`. Responses include `raw_ref` handles referencing attachments (e.g., PDF snippets) that the agent can fetch lazily
5. Agent synthesizes a new weekly summary and calls `reflect` with `replace_scope: "weekly"`, creating a `reflection` referencing the events. Lineage now captures the derivation
6. Later, a different orchestrator connects via MCP, discovers the same node, and repeats the process without bespoke adapters, demonstrating portability

---

## 12. Open Questions & Risks

### Schema Flexibility
Too rigid kills experimentation; too loose becomes mushy JSON blobs.

**Approach:** Core spec with extension slots (`extensions: object?`) + validation rules. Domain-specific fields allowed while maintaining core interoperability.

### Sync & Conflict Resolution
Once you go beyond a single device: how to refer to the "same" person and merge partial context?

**Current approach:** DID-based identity anchoring. Multi-device sync deferred to v0.2.

### Token Format
Neither spec yet specifies a concrete token format.

**Open:** JWT with PCP-specific claims? OAuth2 scopes? Custom format?

### Adoption Incentives
Vendors need a reason to adopt PCP.

**Hypothesis:** Position as the easiest way for agents to deliver value—they get rich personal signals via a standard interface. The value prop is to agent builders, not end users directly.

### Reflect: Data Layer vs Service Layer (v0.2)

Should `reflect` couple PCP to an LLM backend, or should that be the consuming agent's responsibility?

**Current approach (v0.1):** PCP node calls Claude directly to generate reflections.
- Pro: Self-contained, works for any consumer regardless of LLM access
- Con: Couples PCP to a specific LLM backend, requires API key in node

**Alternative:** PCP gathers context only, consuming agent generates reflection.
- Pro: PCP stays a pure data layer
- Con: Requires agents to have their own LLM access

**Proposed v0.2 solution:** Support both via optional `generate` flag:

```yaml
# pcp/reflect parameters
prompt:   string
generate: boolean?  # default: true (node generates reflection)
                    # false = return context only, let agent synthesize
```

This way:
- Lightweight agents get turnkey reflections (current behavior)
- Sophisticated agents can request raw context and do their own synthesis
- PCP remains useful across the capability spectrum

---

## 13. Implementation Roadmap

### Phase 1: Reference Implementation (MVP)
Build a PCP-compliant system:

**Deliverables:**
- [x] Envelope + four object schemas implemented with validation
- [x] `describe/query/observe/learn/reflect` endpoints available over HTTPS JSON
- [x] MCP server exposing PCP tools (using FastMCP pattern)
- [x] Tokens scoped to disclosure levels with audit logging
- [ ] Reference collector (activity monitor daemon) emitting events
- [x] Reference agent (RLM-style loop) that issues `query` then `reflect` using progressive disclosure

**Implementation Notes:**
- MCP server uses FastMCP (mcp.server.fastmcp) for automatic tool schema generation and compliance
- Tools: `pcp_describe`, `pcp_query`, `pcp_observe`, `pcp_learn`, `pcp_reflect`
- HTTP server uses FastAPI on port 6001
- Storage is JSONL append-only (SQLite planned for v0.2)

### Phase 2: Extract & Document
- Formalize what worked into a standalone spec document
- Document the protocol for external consumption
- Identify gaps and iterate based on usage

### Phase 3: Validate Portability
- Can other agents (Claude Code, etc.) use our PCP server?
- Can our agent use other PCP-compliant sources?
- Does the protocol actually enable the portability thesis?

---

## v0.2 Roadmap: Remote Access & Third-Party Agents

v0.1 delivers local-first PCP. v0.2 unlocks the full `pcp://me` vision: any agent, anywhere, can resolve your personal context with appropriate permissions.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        pcp://me                              │
│                           ↓                                  │
│              Resolution (Well-known / DID)                   │
│                           ↓                                  │
│              https://pcp.example.com/api                     │
│                           ↓                                  │
│              Auth Gateway (OAuth2 + Scopes)                  │
│                           ↓                                  │
│              Redaction Layer (per-scope policies)            │
│                           ↓                                  │
│                      PCP Node                                │
└─────────────────────────────────────────────────────────────┘
                            ↑
        ┌───────────────────┼───────────────────┐
        │                   │                   │
   Third-Party         Work Agent          Local Agent
   (Notion, etc.)      (work laptop)       (personal)
```

### 1. Discovery & Resolution

How `pcp://me` resolves to an actual endpoint:

**Option A: Well-known endpoint**
```
GET https://milesgoscha.com/.well-known/pcp
→ {
    "endpoint": "https://pcp.milesgoscha.com/api",
    "node_id": "pcp://milesgoscha",
    "auth": "https://pcp.milesgoscha.com/oauth/authorize"
  }
```

**Option B: DID-based resolution**
```
did:web:milesgoscha.com → DID Document
  → service[type="PCP"] → endpoint
```

**Option C: DNS records**
```
_pcp.milesgoscha.com TXT "endpoint=https://pcp.milesgoscha.com/api"
```

Recommendation: Support well-known as primary, DID as optional enhancement.

### 2. Hosting Patterns

| Pattern | Description | Trust Model |
|---------|-------------|-------------|
| **Self-hosted cloud** | Run PCP node on your own server | Full control |
| **Tunnel** | Expose local node via ngrok/Tailscale/Cloudflare | Data stays local |
| **Hosted service** | PCP-as-a-service provider | Trust provider |
| **Hybrid** | Local node syncs subset to cloud | Selective exposure |

Local access MUST remain first-class—cloud is optional, not required.

### 3. OAuth2 Token Grants

Third-party agents request access via OAuth2 flow:

```
1. Agent → GET pcp://me/.well-known/pcp → discovers auth endpoint
2. Agent → Redirect user to auth endpoint
3. User reviews requested scopes, approves/denies
4. Agent receives scoped token
5. Agent uses token to call PCP operations
```

**Scope request example:**
```json
{
  "client_id": "notion-agent",
  "scopes_requested": [
    "query:event.summary",
    "query:learning.summary",
    "learn:write"
  ],
  "reason": "Sync your notes context with Notion"
}
```

User sees: "Notion Agent wants to read your event summaries, learning summaries, and store learnings. Allow?"

### 4. Redaction Policies

Scoped tokens map to redaction policies that filter/transform data before disclosure:

```yaml
# Example policy for "work-agent" scope
work-agent:
  allowed_types: [event, learning]
  disclosure_max: summary          # Never expose detail/raw
  redact_fields:
    - "payload.detail.url"         # Hide URLs
    - "payload.detail.window_title" # Hide window titles
  tag_filter:
    include: [work]                # Only work-tagged items
    exclude: [personal, health]    # Never personal/health
  time_window: 30d                 # Only last 30 days
```

Redaction is applied server-side before response—agents never see redacted data.

### 5. Trust Tiers

Define explicit trust levels with recommended defaults:

| Tier | Description | Token Lifetime | Max Disclosure | Redaction | Example |
|------|-------------|----------------|----------------|-----------|---------|
| **Local** | Same machine, MCP stdio | 24h+ | raw | None | Claude Code on laptop |
| **First-party remote** | User's own remote agents | 1-24h | detail | Minimal | Work laptop agent |
| **Third-party** | External service agents | 1h | summary | Full | Notion, Zapier |

**Recommended defaults by tier:**

```yaml
local:
  disclosure_max: raw
  token_lifetime: 24h
  redactions: []
  auto_approve: true  # No consent UI needed

first_party_remote:
  disclosure_max: detail
  token_lifetime: 8h
  redactions:
    - "payload.detail.url"  # Hide exact URLs
  auto_approve: false  # Require initial consent

third_party:
  disclosure_max: summary
  token_lifetime: 1h
  redactions:
    - "payload.detail.*"    # Hide all detail fields
    - "envelope.lineage.*"  # Hide provenance
  auto_approve: false
  require_registration: true  # Agent must be registered
```

### 6. Grant Request/Approval Flow

Agents request access; users approve via UI/CLI before tokens are issued:

```
┌─────────────────────────────────────────────────────────────┐
│  1. Agent discovers node                                     │
│     GET https://example.com/.well-known/pcp                 │
│     → { endpoint, auth_endpoint, grants_endpoint }          │
│                                                              │
│  2. Agent requests grant                                     │
│     POST /api/grants/request                                │
│     {                                                        │
│       "client_id": "notion-agent",                          │
│       "client_name": "Notion Integration",                  │
│       "scopes_requested": ["query:event.summary"],          │
│       "reason": "Sync your activity to Notion",             │
│       "callback_url": "https://notion.so/oauth/callback"    │
│     }                                                        │
│     → { "grant_id": "gr_abc123", "status": "pending" }      │
│                                                              │
│  3. User reviews in UI/CLI                                   │
│     pcp grants list                                          │
│     pcp grants approve gr_abc123 --scopes query:event.summary│
│     pcp grants deny gr_abc123 --reason "Don't trust"        │
│                                                              │
│  4. On approval, agent receives token                        │
│     POST /api/grants/gr_abc123/token                        │
│     → { "token": "pcp_...", "expires_at": "..." }           │
│                                                              │
│  5. Audit log records grant + all subsequent access          │
└─────────────────────────────────────────────────────────────┘
```

**Grant states:** `pending` → `approved` / `denied` / `expired`

**User can:**
- Modify requested scopes before approving
- Set custom token lifetime
- Add additional redactions
- Revoke grant at any time

### 7. Scope-Tied Redactions

Redactions are automatically applied based on token's trust tier and scopes:

```python
# Pseudocode: redaction at query time
def apply_redactions(item, token):
    policy = get_redaction_policy(token.trust_tier)

    for field_path in policy.redact_fields:
        remove_field(item, field_path)

    # Enforce disclosure ceiling
    if token.max_disclosure < item.disclosure_level:
        item = downgrade_to(item, token.max_disclosure)

    # Tag filtering
    if policy.tag_filter.exclude:
        if any(tag in item.tags for tag in policy.tag_filter.exclude):
            return None  # Filter out entirely

    return item
```

**Key principle:** Third-party agents automatically get sanitized payloads. They never see raw/detail data they weren't granted. Local agents keep full access. Redaction is server-side and invisible to the consuming agent.

### v0.2 Deliverables

**Discovery & Resolution:**
- [x] `.well-known/pcp` discovery endpoint returning `{ endpoint, auth, grants, public_key }`
- [ ] DID document support (optional): `did:web:example.com` → PCP service endpoint

**Grant Flow:**
- [x] `POST /api/grants/request` - Agent requests access
- [x] `GET /api/grants` - List pending/active grants
- [x] `POST /api/grants/{id}/approve` - Approve with optional scope modification
- [x] `POST /api/grants/{id}/deny` - Deny with reason
- [x] `POST /api/grants/{id}/revoke` - Revoke active grant
- [x] `POST /api/grants/{id}/token` - Issue token for approved grant (with claim_secret security)
- [x] CLI: `pcp grants list|approve|deny|revoke`

**Trust & Redaction:**
- [x] Trust tier classification in token metadata
- [x] Redaction policy engine tied to tiers/scopes (`src/pcp/auth/redactions.py`)
- [x] Per-tier defaults for disclosure, lifetime, redactions
- [x] Tag-based filtering (include/exclude) in query

**Infrastructure:**
- [x] Cloud deployment guide (self-hosted + tunnel options) - `docs/DEPLOY.md`
- [x] Token persistence across restarts (`TokenStore` in `src/pcp/auth/tokens.py`)
- [x] Docker + Caddy auto-SSL deployment
- [ ] Token management UI (view/revoke grants) - optional
- [ ] Agent registry for third-party verification - optional

**Status:** v0.2 core complete. Optional UI/registry deferred.

---

## 14. MVP Checklist

Version 0.1 of PCP is considered complete when:

- [x] Envelope + four object schemas (identity, event, learning, reflection) implemented with validation
- [x] `describe/query/observe/learn/reflect` endpoints available over HTTPS JSON
- [x] MCP tool signatures implemented and documented (FastMCP)
- [x] Tokens scoped to disclosure levels with audit logging
- [x] Reference collector (CLI) that emits `application.switch` and `application.navigation` events
- [x] Reference agent (e.g., simple RLM loop) that issues `query` then `reflect` using progressive disclosure
- [x] Identity queryable via `/api/identity`
- [x] Progressive disclosure working (summary → detail)
- [x] `detail_available` / `raw_available` / `disclosure_level` flags in responses

**Status:** 9/9 complete. MVP done.

---

## v0.3 Roadmap: Hosted PCP Service

v0.2 enables self-hosting. v0.3 unlocks mainstream adoption: users who don't want to run infrastructure can host their context on a managed service while retaining ownership and portability.

### The Problem

Self-hosting works for power users but creates friction:
- Need a server or always-on machine
- DNS/domain configuration
- SSL certificate management
- Backup responsibility

Most users won't do this. For PCP to achieve "any agent can access pcp://me", we need a hosted option.

### Architecture: Managed Single-Tenant

Each user gets their own isolated PCP node container. The control plane manages provisioning and lifecycle; the data plane is the same Docker image used for self-hosting.

```
┌─────────────────────────────────────────────────────────────┐
│                     Control Plane                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │    Auth     │  │ Provisioner │  │  Dashboard  │          │
│  │ (users/JWT) │  │  (Docker)   │  │  (Web UI)   │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         ┌─────────┐    ┌─────────┐    ┌─────────┐
         │ alice's │    │  bob's  │    │ miles's │
         │  node   │    │  node   │    │  node   │
         │ :6001   │    │ :6001   │    │ :6001   │
         └─────────┘    └─────────┘    └─────────┘
              │               │               │
         pcp-alice       pcp-bob        pcp-miles
          volume          volume          volume
```

**Key properties:**
- Reuses existing `pcp:latest` Docker image unchanged
- Per-user Docker volume for data isolation
- Traefik/Caddy routes `{username}.pcp.example.com` to user's container
- Control plane holds encrypted admin tokens to manage each node
- Users interact via standard PCP protocol; no special hosted-mode APIs

### Identity Resolution

**v0.3: Subdomain-based (ship first)**
```
pcp://milesgoscha → https://milesgoscha.pcp.example.com/.well-known/pcp
```
- Wildcard DNS + Traefik labels
- Automatic TLS via Let's Encrypt
- User gets memorable, stable URL

**Future: DID support (add when needed)**
```
pcp://milesgoscha → did:web:pcp.example.com:milesgoscha → DID Document → endpoint
```
- Enables migration from hosted → self-hosted without breaking agent references
- Cryptographic identity verification
- Federation between multiple PCP hosting providers
- Add when: users want to migrate, or multiple hosting services exist

### User Onboarding Flow

```
1. SIGNUP (30 seconds)
   ├── User visits pcp.example.com
   ├── Email + password (or OAuth)
   ├── Choose username → becomes subdomain
   └── Validates uniqueness

2. PROVISIONING (automatic, ~30 seconds)
   ├── Create user record in control plane DB
   ├── Spin up container: pcp:latest
   ├── Mount per-user volume
   ├── Inject env vars:
   │   - PCP_PUBLIC_URL=https://{username}.pcp.example.com
   │   - PCP_NODE_ID=pcp://{username}
   ├── Configure Traefik routing labels
   ├── Wait for health check
   ├── Generate admin token, encrypt, store in control plane
   └── User sees "Ready!"

3. FIRST RUN
   ├── Dashboard shows endpoint URL and node ID
   ├── Guided setup:
   │   1. Set identity (name, timezone)
   │   2. Connect first agent (MCP config snippet)
   │   3. Optional: install collector
   └── Empty states link to docs

4. ONGOING USE
   ├── Dashboard: manage grants, view audit logs, issue tokens
   ├── Agents: standard PCP protocol to user's subdomain
   └── Data export: full dump for migration/backup
```

### Admin Token Security

The control plane holds admin tokens for each user's node. This is the highest-value target.

```
┌─────────────────────────────────────────────────────────────┐
│  Control Plane DB                                            │
│                                                              │
│  nodes table:                                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ user_id │ admin_token_encrypted │ token_version │ ... │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  Security measures:                                          │
│  - Tokens encrypted at rest (per-row key or envelope enc)   │
│  - Master key in HSM or secrets manager (not in DB)         │
│  - Tokens never logged or exposed in API responses          │
│  - Dashboard decrypts on-demand, calls node, discards       │
│                                                              │
│  Rotation:                                                   │
│  - User can rotate via dashboard                            │
│  - Generates new token on node, updates encrypted value     │
│  - Old token immediately invalid                            │
│                                                              │
│  Incident response:                                          │
│  - If DB compromised: tokens encrypted, need master key     │
│  - Can force-rotate all tokens as remediation               │
└─────────────────────────────────────────────────────────────┘
```

### v0.3 Deliverables

**Control Plane (P0):**
- [ ] User auth: email/password signup, JWT sessions
- [ ] Node provisioning: Docker API, per-user volume, env injection
- [ ] Traefik routing: wildcard subdomain + automatic TLS
- [ ] Admin token storage: encrypted in DB, rotation support
- [ ] Health monitoring: detect and restart unhealthy nodes

**Dashboard (P0):**
- [ ] Onboarding wizard: identity setup, agent connection guide
- [ ] Grant management: list, approve, deny, revoke (calls user's node)
- [ ] Audit log viewer: paginated access history
- [ ] Token management: issue local tokens, rotate admin token
- [ ] Settings: change password, delete account

**Data Portability (P1):**
- [ ] Full data export: download all objects as JSONL
- [ ] Account deletion: purge container, volume, and DB records
- [ ] Migration guide: export from hosted, import to self-hosted

**Deferred (P2):**
- [ ] Billing: Stripe integration, usage-based or flat-rate
- [ ] DID support: `did:web` generation, resolution endpoint
- [ ] Multi-region: deploy nodes in user's preferred region
- [ ] Agent registry: verified third-party agent directory

### Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Orchestration | Docker + Traefik (single host) | Simple, scales to ~50 users. Migrate to K8s when needed. |
| Database | PostgreSQL | Control plane state, user accounts, encrypted tokens |
| Key management | Secrets manager (e.g., AWS Secrets Manager) | Master key not in DB |
| Container registry | Private registry or Docker Hub | Pull pcp:latest on provision |
| Monitoring | Prometheus + Grafana | Per-node health, control plane metrics |

### Open Questions (Resolved)

1. **Key custody**: Service-managed initially. Users trust the service with their signing key. User-managed keys can be added later for advanced users.

2. **Pricing model**: Deferred. Launch free, add billing when there's traction.

3. **Federation timeline**: Not needed for v0.3. Single hosted service. Add DID when users want to migrate or other hosts exist.

4. **Data residency**: Single region for v0.3. Multi-region as P2 enhancement.
