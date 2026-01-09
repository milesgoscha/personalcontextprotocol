# PCP - Personal Context Protocol

**"Personal context needs structure to be portable."**

PCP is a transport-independent specification for representing and governing
personal context (identity, events, learnings, reflections). The spec lives in
`docs/SPEC.md` and defines the object model, provenance requirements,
capabilities, scope semantics, and audit invariants. Any datastore or transport
that satisfies that contract is PCP-compliant.

This repository also ships a **reference node**: a FastAPI server plus CLI,
collectors, and MCP bindings that implement the spec (PCP-Core + PCP-Extended).
Think of MCP as one possible transport profile; disabling MCP does not change
the semantics defined by the spec.

## OAuth 2.1 (Optional Transport Profile)

Hosted deployments now expose PCP via OAuth 2.1 so third-party MCP clients
(e.g., Claude Code) can authenticate without manual Bearer tokens. Key details:

- Discovery: `https://<pcp-domain>/.well-known/oauth-authorization-server` and
  `https://<username>.<pcp-domain>/.well-known/oauth-protected-resource`.
- Dynamic registration (`POST /oauth/register`) issues public clients that must
  use PKCE (`S256`).
- Scopes are mapped to PCP scopes via an allowlist (read-only by default; write
  scopes require explicit consent). See Appendix A of `docs/SPEC.md`.
- Authorization → Token flow auto-approves a matching PCP grant and returns a
  scoped PCP token plus refresh token. Refreshes revoke the previous PCP token
  before issuing a new one.

If you operate your own control plane, follow the instructions in
`hosted/control/routes/oauth.py` and `docs/SPEC.md` to expose the OAuth surface;
standalone/self-hosted nodes can ignore this section.

## Project Structure

```
pcp/
├── docs/
│   └── SPEC.md              # Full protocol specification
├── src/pcp/
│   ├── models/              # Data models with validation
│   │   ├── envelope.py      # Common envelope wrapper
│   │   ├── identity.py      # Identity payload
│   │   ├── event.py         # Event payload
│   │   ├── learning.py      # Learning payload
│   │   └── reflection.py    # Reflection payload
│   ├── server/              # PCP Node server
│   │   ├── app.py           # FastAPI application
│   │   ├── operations.py    # describe/query/observe/learn/reflect
│   │   └── storage.py       # Persistence layer
│   ├── auth/                # Security & audit (baked in from v0.1)
│   │   ├── scopes.py        # Scope definitions & validation
│   │   ├── tokens.py        # Token generation & verification
│   │   └── audit.py         # Audit event logging
│   ├── mcp/                 # MCP server adapter
│   │   └── server.py        # Expose PCP tools via MCP
│   ├── collectors/          # Reference collectors
│   │   └── activity.py      # Activity monitor collector
│   ├── agents/              # Reference agents
│   │   └── rlm_agent.py     # RLM-style agent (query → reflect loop)
│   └── cli.py               # CLI entry point
├── examples/
│   └── basic_flow.py        # End-to-end demo
├── pyproject.toml
└── README.md
```

## Quick Start

```bash
# Install
cd pcp
pip install -e ".[dev]"

# Start PCP node (default: localhost:6001)
pcp server start

# In another terminal, run reference agent
pcp agent run --prompt "What did I work on today?"
```

## Architecture

```
┌─────────────┐     observe      ┌──────────┐     query/reflect     ┌─────────────┐
│  Collectors │ ───────────────► │ PCP Node │ ◄─────────────────── │   Agents    │
│  (activity) │                  │          │                       │ (RLM-style) │
└─────────────┘                  │  ┌─────┐ │                       └─────────────┘
                                 │  │Auth │ │
                                 │  │Audit│ │
                                 │  └─────┘ │
                                 └──────────┘
                                       │
                                       ▼
                              pcp://me endpoint
                              (HTTPS or MCP)
```

## Auth & Audit (Built-in from v0.1)

Security is not bolted on—it's core to the design:

### Scoped Tokens
```python
# Tokens enumerate explicit scopes
token = pcp.auth.create_token(
    scopes=["query:event.summary", "query:learning.*"],
    expires_in=timedelta(hours=1)
)
```

### Audit Trail
```python
# Every operation is logged as a PCP event
# under the reserved pcp.audit.* namespace
{
    "type": "event",
    "event_kind": "pcp.audit.query",
    "detail": {
        "requester": "agent:research",
        "operation": "query",
        "object_types": ["event"],
        "disclosure": "summary",
        "result_count": 42
    }
}
```

### Disclosure Control
- Tokens can limit disclosure level (e.g., `summary-only`)
- Redaction policies per scope
- `detail_available` / `raw_available` flags in responses

## Reference Agent

The reference agent (`src/pcp/agents/rlm_agent.py`) demonstrates the RLM pattern:

```python
# Simplified RLM loop
async def rlm_loop(prompt: str):
    # 1. Query at summary level
    events = await pcp.query(
        type="events",
        timerange={"after": "today"},
        disclosure="summary"
    )

    # 2. Drill down on high-salience items
    for event in events.items:
        if needs_detail(event):
            detail = await pcp.query(
                ids=[event.id],
                disclosure="detail"
            )

    # 3. Synthesize reflection
    reflection = await pcp.reflect(
        prompt=prompt,
        context=["events", "learnings"],
        save=True
    )

    return reflection
```

## MVP Checklist

- [ ] Envelope + four object schemas with validation
- [ ] `describe/query/observe/learn/reflect` over HTTPS
- [ ] MCP tool signatures
- [ ] Scoped tokens with audit logging
- [ ] Reference collector (activity monitor)
- [ ] Reference agent (RLM-style loop)
- [ ] `pcp://me/identity` queryable
- [ ] Progressive disclosure (summary → detail → raw)

## License

MIT
