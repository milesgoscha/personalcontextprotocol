# PCP - Personal Context Protocol

## What is PCP?

PCP is a protocol and reference implementation for giving AI agents access to personal context - events, learnings, preferences, and reflections that travel with a user across different AI applications.

Think of it as a personal context server that any AI agent can connect to (with permission) to understand who you are and what you've been doing.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Hosted Control Plane                        │
│         (user signup, node provisioning, dashboard)          │
│                    hosted/control/                           │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         ┌─────────┐    ┌─────────┐    ┌─────────┐
         │ User A  │    │ User B  │    │ User C  │
         │  Node   │    │  Node   │    │  Node   │
         │(pcp:*)  │    │(pcp:*)  │    │(pcp:*)  │
         └─────────┘    └─────────┘    └─────────┘
```

**Two main components:**

1. **PCP Node** (`src/pcp/`) - The personal context server
   - Stores events, learnings, reflections, identity
   - Exposes REST API and MCP endpoint
   - Runs as Docker container with persistent volume

2. **Hosted Control Plane** (`hosted/control/`) - Multi-tenant hosting
   - User signup/auth (JWT + Argon2)
   - Node provisioning via Docker API
   - Dashboard for grant management, token creation
   - Proxies admin requests to user nodes

## Key Directories

```
src/pcp/
├── server/
│   ├── app.py          # FastAPI app, mounts MCP at /mcp
│   ├── operations.py   # Core PCP operations (query, observe, learn, reflect)
│   └── storage.py      # JSON file storage backend
├── auth/
│   ├── tokens.py       # Token creation/verification
│   ├── grants.py       # Grant request/approval flow
│   └── scopes.py       # Scope validation (query:event.*, observe:event, etc.)
├── mcp/
│   ├── sse.py          # MCP HTTP endpoint (Streamable HTTP transport)
│   └── remote.py       # MCP client for remote nodes (stdio transport)
└── models/
    └── envelope.py     # Object types (event, learning, reflection, identity)

hosted/
├── control/
│   ├── app.py          # Control plane FastAPI app
│   ├── routes/
│   │   ├── auth.py     # /signup, /login, /logout
│   │   ├── dashboard.py # HTML pages (HTMX + Jinja2)
│   │   ├── nodes.py    # Node lifecycle API
│   │   └── proxy.py    # Proxy to user's node for grants/tokens
│   ├── services/
│   │   ├── provisioner.py   # Docker container management
│   │   └── node_client.py   # HTTP client to user nodes
│   └── templates/      # Jinja2 + HTMX templates
├── docker/
│   ├── docker-compose.dev.yml    # Local dev with Traefik
│   └── docker-compose.hosted.yml # Production setup
└── migrations/         # Alembic migrations for PostgreSQL
```

## PCP Scopes

Scopes control what operations a token can perform:

- `query:event.*` - Query events at any disclosure level
- `query:event.summary` - Query events, summary only
- `query:learning.*` - Query learnings
- `query:reflection.*` - Query reflections
- `query:identity` - Query identity
- `observe:event` - Record events
- `learn:write` - Store learnings
- `reflect:write` - Generate reflections
- `pcp:admin` - Full admin access

## Development

### Running locally (hosted mode)

```bash
cd hosted/docker
docker compose -f docker-compose.dev.yml up -d
```

This starts:
- PostgreSQL (control plane database)
- Traefik (reverse proxy, routes *.pcp.localhost)
- Control plane (pcp.localhost)
- User nodes get created as pcp-{username} containers

### Running a standalone node

```bash
docker build -t pcp:latest .
docker run -d -p 6001:6001 -v pcp-data:/data pcp:latest
```

### Testing

```bash
pytest tests/
```

### Key environment variables

**PCP Node:**
- `PCP_NODE_ID` - Node identifier (e.g., `pcp://miles`)
- `PCP_DATA_DIR` - Data directory (default: `/data`)
- `PCP_ALLOW_INITIAL_TOKEN` - Allow unauthenticated token creation (dev only)
- `ANTHROPIC_API_KEY` - Required for reflect operation

**Control Plane:**
- `DATABASE_URL` - PostgreSQL connection string
- `JWT_SECRET` - Secret for JWT signing
- `ENCRYPTION_KEY` - For encrypting admin tokens at rest
- `PCP_DOMAIN` - Domain for user subdomains (e.g., `pcp.localhost`)

## MCP Integration

Agents connect to PCP via MCP (Model Context Protocol):

```json
{
  "mcpServers": {
    "pcp": {
      "url": "http://miles.pcp.localhost/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

Available MCP tools:
- `pcp_describe` - Get node capabilities
- `pcp_query` - Query events, learnings, reflections, identity
- `pcp_observe` - Record events
- `pcp_learn` - Store durable facts
- `pcp_reflect` - Generate reflections (requires ANTHROPIC_API_KEY)

## Common Tasks

**Create a token (dev node):**
```bash
curl -X POST http://localhost:6001/api/token \
  -H "Content-Type: application/json" \
  -d '{"subject": "my-agent", "scopes": ["query:event.*", "observe:event"]}'
```

**Query events:**
```bash
curl http://localhost:6001/api/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"object_types": ["event"], "disclosure": "summary", "limit": 10}'
```

**Record an event:**
```bash
curl -X POST http://localhost:6001/api/observe \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"objects": [{"envelope": {"type": "event"}, "payload": {"event_kind": "note", "summary": "Test event"}}]}'
```

## Production Deployment

See `.claude/deployment.md` for server access and deployment instructions (gitignored for security).

### Production URLs

- Dashboard: https://pcp.bio
- User nodes: https://{username}.pcp.bio
- MCP endpoint: https://{username}.pcp.bio/mcp
