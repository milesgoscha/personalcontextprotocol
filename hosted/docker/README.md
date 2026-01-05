# PCP Hosted Service Deployment

This directory contains Docker configurations for deploying the PCP Hosted Service.

## Local Development (Quick Start)

Test the hosted service locally without a domain or TLS:

```bash
# 1. Build the PCP node image (from repo root)
cd /path/to/pcp
docker build -t pcp:latest .

# 2. Set up dev environment
cd hosted/docker
cp .env.dev.example .env

# 3. Start services (migrations run automatically before control plane)
docker compose -f docker-compose.dev.yml up -d

# 4. Access the service
open http://pcp.localhost
```

**Local URLs:**
- Control plane: http://pcp.localhost
- User nodes: http://{username}.pcp.localhost
- Traefik dashboard: http://localhost:8080
- PostgreSQL: localhost:5432

**Note:** Modern browsers handle `*.localhost` wildcards automatically. If you have issues, add to `/etc/hosts`:
```
127.0.0.1 pcp.localhost
127.0.0.1 alice.pcp.localhost
```

---

## Production Deployment

## Prerequisites

- Docker and Docker Compose v2+
- A domain name with DNS control (for wildcard SSL)
- The `pcp:latest` image built and available

## Quick Start

1. **Copy the environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` with your settings:**
   - Set `PCP_DOMAIN` to your domain
   - Generate secrets:
     ```bash
     # JWT secret
     openssl rand -hex 32

     # Encryption key
     openssl rand -hex 32

     # PostgreSQL password
     openssl rand -base64 24
     ```
   - Configure DNS provider credentials for Let's Encrypt

3. **Build the PCP node image** (from repo root):
   ```bash
   docker build -t pcp:latest .
   ```

4. **Build and start services:**
   ```bash
   docker compose -f docker-compose.hosted.yml up -d
   ```

5. **Run database migrations:**
   ```bash
   docker compose -f docker-compose.hosted.yml run --rm migrations
   ```

6. **Verify the deployment:**
   ```bash
   curl https://your-domain.com/health
   ```

## Architecture

```
Internet
    │
    ▼
┌─────────┐
│ Traefik │ (ports 80, 443)
└────┬────┘
     │
     ├──────────────────────┐
     │                      │
     ▼                      ▼
┌─────────────┐    ┌──────────────┐
│Control Plane│    │ User Nodes   │
│ (FastAPI)   │    │ (pcp:latest) │
└──────┬──────┘    └──────────────┘
       │
       ▼
┌──────────┐
│PostgreSQL│
└──────────┘
```

## SSL Certificates

The setup uses Let's Encrypt with DNS challenge for wildcard certificates.
This allows automatic SSL for all user subdomains (`*.pcp.example.com`).

### Supported DNS Providers

- Cloudflare (recommended)
- AWS Route53
- DigitalOcean
- Google Cloud DNS
- [Many more...](https://doc.traefik.io/traefik/https/acme/#providers)

## Scaling

For production deployments with many users:

1. **Horizontal scaling**: Run multiple control plane instances behind Traefik
2. **Database**: Use managed PostgreSQL (RDS, Cloud SQL, etc.)
3. **Monitoring**: Add Prometheus metrics endpoint
4. **Alerting**: Configure health check alerts

## Backup

### Database
```bash
docker exec pcp-postgres pg_dump -U pcp pcp_hosted > backup.sql
```

### User Data Volumes
```bash
# List all user volumes
docker volume ls | grep pcp-data-

# Backup a specific user's data
docker run --rm -v pcp-data-username:/data -v $(pwd):/backup alpine \
    tar czf /backup/username-backup.tar.gz -C /data .
```

## Troubleshooting

### Check logs
```bash
# Control plane logs
docker logs pcp-control-plane

# Traefik logs
docker logs pcp-traefik

# Specific user node
docker logs pcp-username
```

### Database connection issues
```bash
# Test PostgreSQL connection
docker exec pcp-postgres pg_isready -U pcp
```

### Certificate issues
```bash
# Check Traefik dashboard (if enabled)
# Or check certificate status
docker exec pcp-traefik cat /letsencrypt/acme.json | jq '.letsencrypt.Certificates'
```

## Security Notes

- Never commit `.env` files to version control
- Rotate `JWT_SECRET` periodically (invalidates all sessions)
- Rotate `MASTER_ENCRYPTION_KEY` using key versioning
- Keep Docker and all images updated
- Enable firewall, only expose ports 80 and 443
