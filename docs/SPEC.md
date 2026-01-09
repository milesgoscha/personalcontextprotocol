# Personal Context Protocol (PCP) — Specification v0.2 (Draft)

> This document is the normative definition of PCP. It describes the semantics of
> personal-context data, the invariants that govern it, and the minimum behavior
> required for conformant implementations. Implementation notes and reference
> architectures are informative unless marked otherwise.

PCP was created to answer a single question: **how do AI systems exchange
user-owned context without copying prompts or rewriting bespoke integrations for
every product?** The answer is a transport-independent contract covering objects,
governance, and capability semantics. Software such as the FastAPI “PCP node”
included in this repository is a reference implementation of that contract—it is
not the spec itself.

---

## 0. Status of This Document

This version is a draft that tracks the behavior of the reference node at
`v0.2`. The key words **MUST**, **SHOULD**, and **MAY** are to be interpreted as
described in RFC 2119.

---

## 1. Scope & Motivation (Informative)

- Personal context (identity, events, learnings, reflections, artifacts) is owned
  by the user. It must travel across agents, transports, and time.
- PCP defines meaning, not software: any datastore, transport, or auth system
  can implement PCP as long as it satisfies the invariants herein.
- MCP, HTTPS, local files, or future protocols are all valid carriers. PCP says
  *what* a request/response means; transports decide *how* messages flow.

---

## 2. Conformance Levels (Normative)

| Level        | Required Sections                                                  |
|--------------|--------------------------------------------------------------------|
| **PCP-Core** | §4 Core Object Model, §5 Capability Semantics, §6 Scope Model, §8 Identity |
| **PCP-Extended** | PCP-Core + §7 Temporal Semantics + §9 Audit                     |
| **PCP-Strict** | PCP-Extended + §4.4 Derived Context Rules + §10 Transport Clause |

Implementations **MUST** state which level they target (e.g., “PCP-Core +
Extended with optional Decay”). Features beyond PCP-Core **MUST NOT** weaken the
core invariants.

### 2.1 Spec Metadata Advertisement

Conformant implementations **MUST** publish their spec metadata in discovery
responses:

- `/.well-known/pcp` **MUST** include `spec.version` and `spec.conformance`.
- The `describe` operation **MUST** mirror those fields.
- Other public endpoints that return PCP objects or governance artifacts
  (tokens, grants, audit events, import/export responses) **SHOULD** include
  a `spec` reference so downstream agents can cite the governing clauses.

---

## 3. Terminology (Normative)

- **Context Object** – atomic unit of personal context governed by this spec.
- **Owner** – human subject the Context Object belongs to (e.g., DID or account).
- **Actor** – human, agent, or service accessing or producing context.
- **Capability** – semantic permission (READ, DERIVE, APPEND, MUTATE, SCOPE_QUERY).
- **Scope** – constraint over which Context Objects or disclosures may be used.
- **Provenance** – metadata describing how an object was created or derived.

---

## 4. Core Object Model (Normative)

### 4.1 Context Object

Every Context Object **MUST** include the fields below. Labels are conceptual;
implementations may map them to schema-specific names.

| Field        | Description                                                       |
|--------------|-------------------------------------------------------------------|
| `id`         | Globally unique, stable identifier (URI recommended).             |
| `owner`      | Canonical identifier of the human subject (exactly one).         |
| `type`       | Semantic category (`identity`, `event`, `learning`, `reflection`, extension types). |
| `content`    | Payload body (opaque to the spec; MAY be structured).            |
| `created_at` | RFC 3339 timestamp.                                               |
| `updated_at` | RFC 3339 timestamp (may equal `created_at`).                      |
| `provenance` | See §4.2.                                                         |
| `permissions`| Eval-time policy (capabilities, scopes, expiration).              |
| `confidence` | Optional float [0,1]; **SHOULD** be present for derived objects.  |

**Invariant 4.1.a – Single Ownership:** Context Objects **MUST** have exactly one
owner. Ownership **MAY NOT** transfer without explicit user consent recorded in
audit logs.

### 4.2 Provenance

Provenance **MUST** include:

- `source ∈ {human, ai, system, imported}`
- `derivation ∈ {direct, inferred, summarized, transformed}`
- `inputs` – references to upstream Context Objects when derivation ≠ direct
- `timestamp` – creation instant
- `agent_id` – identifier for the non-human creator (collector, agent, tool)

**Invariant 4.2.a – Traceability:** Derived Context Objects **MUST** have a
traceable provenance chain. If an upstream object is revoked, derived objects
**MUST** be marked invalid, recomputed, or deleted.

### 4.3 Envelope & Payload

PCP encourages an envelope/payload structure:

- **Envelope:** transport-agnostic metadata (id, owner, type, provenance,
  permissions, disclosure metadata, tags, extensions).
- **Payload:** object-type-specific body (identity facts, event detail, etc.).

Envelopes **MUST** declare disclosure tiers available (`summary`, `detail`, `raw`
at minimum) and whether additional levels exist. Payloads **SHOULD** provide
`summary` fields ≤1 KB for efficient scanning.

### 4.4 Derived Context Rules (PCP-Strict)

- A Derived Context Object **MUST** reference all inputs via canonical IDs.
- If any input is revoked or deleted, the derived object **MUST** transition to
  `invalid` state until recomputed.
- Derived objects **MUST** record assumptions (e.g., model version, prompt,
  filters) sufficient to audit reproducibility.

---

## 5. Capability & Permission Semantics (Normative)

PCP defines semantic permissions; enforcement mechanisms are out of scope.

### 5.1 Capability Taxonomy

| Capability   | Meaning                                                           |
|--------------|-------------------------------------------------------------------|
| `READ`       | Access raw payload at allowed disclosure levels.                  |
| `DERIVE`     | Produce summaries/embeddings/transformations from accessed data.  |
| `APPEND`     | Add new Context Objects (write-only).                             |
| `MUTATE`     | Modify or delete existing Context Objects.                        |
| `SCOPE_QUERY`| Discover metadata about what exists without revealing content.    |

**Invariant 5.1.a – No silent edits:** `MUTATE` implies `APPEND` (mutations must
emit provenance) but **MUST NOT** imply `READ`. Implementations **MUST** prevent
actors from rewriting context they cannot see.

### 5.2 Permission Grants

Each permission grant **MUST** specify:

- `actor` (who receives it)
- `capability` (from the taxonomy)
- `scope` (see §6)
- `expiration` timestamp or explicit `indefinite`
- `revocation behavior` (retroactive, prospective)

Grants **MUST** be explicit and **MUST** be revocable. PCP-Extended
implementations **SHOULD** expose grant history via audit events.

### 5.3 Scope Descriptors (PCP-Extended)

To make scopes self-describing across transports, PCP-Extended implementations
**SHOULD** attach scope descriptors whenever they return scopes (e.g., in grants,
token issuance, or manifest documents). A scope descriptor encodes:

```json
{
  "scope": "query:event.summary",
  "operation": "query",
  "capability": "READ",
  "object_type": "event",
  "disclosure": "summary",
  "spec_ref": {
    "capabilities": "PCP §5",
    "scope": "PCP §6"
  }
}
```

These descriptors are purely informational but enable automated validation that
scopes adhere to PCP’s capability taxonomy and scope invariants.

---

## 6. Scope Model (Normative)

Scopes define the slice of context an actor can reach. Scope dimensions include:

- **Temporal:** e.g., last 30 days, `after:2026-01-01`.
- **Topical:** tags or namespaces (project, domain, artifact type).
- **Sensitivity:** classes such as `low`, `medium`, `high`.
- **Task-bound:** limited to a condition (e.g., “only while executing task ABC”).

**Invariant 6.a – Monotonic Reduction:** Derived scopes **MUST NOT** expose more
information than their parents. Example: a scoped agent fetching summaries
cannot escalate to detail without a new grant even if it derives additional
filters.

**Invariant 6.b – Disclosure Ceiling:** Requested disclosure levels must be
capped by both capability and trust tier. Implementations **MUST** signal the
effective level returned.

---

## 7. Temporal Semantics (PCP-Extended)

Context changes validity over time. PCP introduces optional but standardized
fields:

- `valid_until` – timestamp when the object should be reviewed/expired.
- `confidence_decay_function` – expression describing how confidence degrades.
- `review_required_after` – timestamp after which actors **SHOULD NOT** trust the
  object without revalidation.

**Recommendation:** PCP-Extended nodes **SHOULD** treat expired context as
advisory only unless reaffirmed or extended.

---

## 8. Identity & Actors (Normative)

### 8.1 Actors

Actors **MUST** declare:

- Stable identifier (URI recommended).
- Type (`human`, `ai_agent`, `service`).
- Optional attestations (e.g., signed statements, trust tier).

**Invariant 8.a – No self-delegation:** Non-human actors **MUST NOT** grant
themselves new permissions. Human owners or their explicit delegates control
grants.

### 8.2 Identity Objects

Identity Context Objects store stable facts (legal name, DID, timezone). They
**MUST** be readable at summary level by any actor that has any PCP permission so
logs can attribute actions.

---

## 9. Audit & Accountability (PCP-Extended)

Implementations **MUST** emit auditable events for:

- Permission granted / revoked / expired.
- Context created (APPEND).
- Context derived (DERIVE).
- Context mutated or deleted.
- Context accessed (recommended; minimally record actor/disclosure).

Audits **MUST** be attributable to an Actor and **MUST** include:

- `operation`
- `requester`
- `token/grant reference`
- `scope + disclosure level`
- `result count / object ids`
- `timestamp`

Audit records themselves are Context Objects reserved under the namespace
`pcp.audit.*` and inherit the same invariants.

---

## 10. Transport Independence (Normative)

PCP **MUST NOT** mandate any transport, serialization format, hosting model, or
auth mechanism. Compliance is determined by semantic behavior only. Examples:

- HTTPS + JSON API with Bearer tokens (the reference node).
- MCP tool provider exposing PCP operations.
- Local-first file store syncing via git.
- Encrypted graph DB accessible via SQL.

So long as the object model, provenance, capability semantics, scopes, and audit
rules are honored, an implementation is PCP-compliant.

---

## 11. Reference Implementation Notes (Informative)

The `pcp/server/app.py` FastAPI node bundled in this repository targets
**PCP-Extended** compliance:

- Its storage layer persists Context Objects with envelopes matching §4.
- Token scopes map to capability semantics (`query:event.summary` →
  `READ(summary)`).
- Grant workflows enforce explicit actor, capability, scope, expiration.
- Redaction policies implement disclosure ceilings per trust tier.
- Audit logs are emitted as PCP events.
- The MCP adapter is merely a transport binding; disabling MCP does not change
  PCP semantics.

Other implementations—offline stores, vendor-hosted services, or new transports—
can reuse the same spec and still interoperate at the level of meaning.

---

## Appendix A – OAuth 2.1 Interop (Informative)

Some deployments expose PCP via OAuth 2.1 so third-party MCP clients can obtain
scoped tokens without manual Bearer configuration. This appendix documents how
PCP semantics map onto the OAuth flow; it does **not** make OAuth mandatory.

### A.1 Endpoints & Discovery

- Resource servers serve `/.well-known/oauth-protected-resource` on each
  `username.pcp.example.com` subdomain, pointing to the shared authorization
  server.
- The authorization server publishes `/.well-known/oauth-authorization-server`
  (RFC 8414) with `authorization_endpoint`, `token_endpoint`, and
  `registration_endpoint`.
- Clients perform dynamic registration (RFC 7591) and MUST use PKCE (`S256`).

### A.2 Scope Allowlist

OAuth `scope` strings map directly to PCP scopes but are filtered through a
deployment-defined allowlist. The reference control plane ships with:

| Allowed scope         | Capability  | Notes                                      |
|-----------------------|-------------|--------------------------------------------|
| `query:event.summary` | READ        | Default when client omits scope            |
| `query:event.*`       | READ        | Full disclosure for events                 |
| `query:learning.*`    | READ        |                                            |
| `query:reflection.*`  | READ        |                                            |
| `query:identity`      | READ        | Required for audit attribution             |
| `observe:event`       | APPEND      | Requires explicit consent                  |
| `learn:write`         | MUTATE      | Requires explicit consent                  |
| `reflect:write`       | DERIVE      | Requires explicit consent                  |

Any scope outside the allowlist is dropped; if none remain the default
read-only set is used. This ensures OAuth clients cannot request `pcp:admin` or
other internal scopes.

### A.3 Grant & Token Flow

1. User visits `/oauth/authorize` with MCP client parameters.
2. After login/consent, the control plane requests a PCP grant via
   `/api/grants/request`, auto-approves it (subject to trust tier policy), and
   claims a token on behalf of the OAuth client.
3. The OAuth token response returns the PCP token plus a refresh token; refresh
   events revoke the previous PCP token and issue a new grant/token pair.

All tokens minted via OAuth inherit the same audit/logging semantics as direct
PCP tokens and include the scope descriptors described in §5.3.

---

## Appendix B – Example Context Object

```jsonc
{
  "envelope": {
    "id": "pcp://miles/evt/9f1d",
    "owner": "did:key:z6Mk...",
    "type": "event",
    "schema": "pcp.event.v1",
    "created_at": "2026-01-04T17:20:58Z",
    "updated_at": "2026-01-04T17:20:58Z",
    "tags": ["research", "browser"],
    "disclosure": {
      "available_levels": ["summary", "detail", "raw"],
      "default_level": "summary"
    },
    "permissions": {
      "allowed_capabilities": ["READ", "DERIVE"],
      "scope": {"temporal": {"after": "2026-01-01"}},
      "expires_at": "2026-01-31T00:00:00Z"
    },
    "provenance": {
      "source": "system",
      "derivation": "direct",
      "inputs": [],
      "timestamp": "2026-01-04T17:20:58Z",
      "agent_id": "collector:activity_monitor@1.2.0"
    },
    "extensions": {
      "com.example/device": "macbook-pro"
    }
  },
  "payload": {
    "summary": "Visited arxiv.org: Recursive Language Models paper",
    "detail": {
      "application": "Arc",
      "window_title": "Recursive Language Models",
      "url": "https://arxiv.org/abs/2512.24601"
    },
    "raw_ref": {
      "uri": "pcp://miles/blob/raw-evt-9f1d",
      "encoding": "binary",
      "offsets": [{"start": 0, "length": 4096}]
    },
    "confidence": 0.97
  }
}
```

This example illustrates how existing code already aligns with the spec: the
reference node stores identical envelopes, provenance, and disclosure metadata;
agents interact via MCP or HTTPS, but semantics remain unchanged.
