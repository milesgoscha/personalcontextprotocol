# PCP Node Deployment Guide

Deploy your own PCP node in 5 minutes.

## Option 1: Local Development

Run PCP on your machine without Docker:

```bash
# Clone and install
git clone <repo-url>
cd pcp
pip install -e .

# Start the server
pcp server start
```

Access at: `http://localhost:6001`

## Option 2: Docker (Local)

Run PCP in a container locally:

```bash
# Build and run
docker compose -f docker-compose.local.yml up -d

# Check logs
docker logs pcp-node

# Stop
docker compose -f docker-compose.local.yml down
```

Access at: `http://localhost:6001`

Data persists in the `pcp-data` Docker volume.

## Option 3: Cloud Server with Auto-SSL

Deploy to a VPS (DigitalOcean, Linode, etc.) with automatic HTTPS:

### Prerequisites

1. A server with Docker installed
2. A domain pointing to your server (e.g., `pcp.yourdomain.com`)
3. Ports 80 and 443 open

### Deploy

```bash
# Clone the repo on your server
git clone <repo-url>
cd pcp

# Set your domain
export PCP_DOMAIN=pcp.yourdomain.com

# Optional: set a custom node ID
export PCP_NODE_ID=milesgoscha

# Start (Caddy auto-provisions Let's Encrypt certificates)
docker compose up -d

# Check status
docker compose ps
docker logs pcp-caddy
```

Your PCP node is now live at `https://pcp.yourdomain.com`

### Verify

```bash
# Check discovery endpoint
curl https://pcp.yourdomain.com/.well-known/pcp

# Check health
curl https://pcp.yourdomain.com/health
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PCP_DATA_DIR` | `~/.pcp/data` | Persistent data directory |
| `PCP_PUBLIC_URL` | `http://localhost:6001` | Public URL for discovery |
| `PCP_NODE_ID` | `pcp://local` | Node identifier |
| `PCP_DOMAIN` | `localhost` | Domain for Caddy SSL |

## Data Persistence

All PCP data is stored in `/data` inside the container, mapped to the `pcp-data` Docker volume:

- `objects.jsonl` - Events, learnings, reflections
- `grants.json` - Grant requests and approvals
- `tokens.json` - Active tokens
- `signing_key.bin` - Token signing key (keep this safe!)
- `identity.json` - User identity

### Backup

```bash
# Create backup
docker run --rm -v pcp-data:/data -v $(pwd):/backup alpine \
    tar czf /backup/pcp-backup-$(date +%Y%m%d).tar.gz /data

# Restore
docker run --rm -v pcp-data:/data -v $(pwd):/backup alpine \
    tar xzf /backup/pcp-backup-YYYYMMDD.tar.gz -C /
```

## Exposing from Your PC (Tunnel)

If you want to run PCP on your personal computer and expose it to the internet:

### Using ngrok

```bash
# Start PCP locally
pcp server start &

# Expose via ngrok
ngrok http 6001
```

ngrok provides a public HTTPS URL like `https://abc123.ngrok.io`

### Using Cloudflare Tunnel

```bash
# Install cloudflared
brew install cloudflare/cloudflare/cloudflared

# Login (one-time)
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create pcp

# Run
cloudflared tunnel run --url http://localhost:6001 pcp
```

## Security Considerations

1. **Signing Key**: The `signing_key.bin` file is critical. If compromised, attackers can forge tokens. Back it up securely.

2. **Grants**: Third-party agents must request grants. Review pending grants with:
   ```bash
   pcp grants list --status pending
   ```

3. **Trust Tiers**: Third-party agents only get summary-level access by default. Local agents get full access.

4. **Firewall**: Only expose ports 80/443. The PCP port (6001) should only be accessible to the Caddy reverse proxy.

## Troubleshooting

### Caddy not getting certificates

- Ensure your domain's DNS A record points to your server
- Check Caddy logs: `docker logs pcp-caddy`
- Ensure ports 80/443 are open

### Tokens not persisting

- Check the data volume exists: `docker volume ls`
- Verify permissions inside container: `docker exec pcp-node ls -la /data`

### Can't connect from external agent

- Verify `/.well-known/pcp` returns correct public URL
- Check firewall rules
- Ensure HTTPS is working (agents may reject HTTP)
