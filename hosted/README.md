# PCP Hosted Service

Control plane for managed PCP nodes. Provides user authentication, node provisioning, and a web dashboard.

## Development

See [docker/README.md](docker/README.md) for local development setup.

## Architecture

- **Control Plane**: FastAPI server handling auth, provisioning, and dashboard
- **User Nodes**: Individual `pcp:latest` containers per user
- **Traefik**: Reverse proxy with wildcard SSL routing
- **PostgreSQL**: User and node metadata storage
