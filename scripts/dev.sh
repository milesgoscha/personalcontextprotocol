#!/bin/bash
# Local development script for PCP control plane
# Starts Postgres, runs migrations, and launches uvicorn with hot reload

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_ROOT/hosted/docker"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting PCP local development...${NC}"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Docker is not running. Please start Docker first.${NC}"
    exit 1
fi

# Start Postgres if not running
if ! docker ps --format '{{.Names}}' | grep -q 'pcp-postgres-local'; then
    echo -e "${YELLOW}Starting Postgres...${NC}"
    cd "$DOCKER_DIR"
    docker compose -f docker-compose.local.yml up -d

    # Wait for Postgres to be ready
    echo -e "${YELLOW}Waiting for Postgres to be ready...${NC}"
    until docker exec pcp-postgres-local pg_isready -U pcp -d pcp_hosted > /dev/null 2>&1; do
        sleep 1
    done
    echo -e "${GREEN}Postgres is ready!${NC}"
else
    echo -e "${GREEN}Postgres already running${NC}"
fi

# Set environment variables
export DATABASE_URL="postgresql+asyncpg://pcp:devpassword@localhost:5432/pcp_hosted"
export JWT_SECRET="dev-jwt-secret-change-in-production-please"
export MASTER_ENCRYPTION_KEY="dev-encryption-key-32-bytes-ok"
export PCP_DOMAIN="pcp.localhost"
export DEBUG="true"
export MULTI_TENANT="true"
export SHARED_NODE_URL="http://localhost:6001"

# Run migrations
echo -e "${YELLOW}Running database migrations...${NC}"
cd "$PROJECT_ROOT/hosted"
alembic upgrade head
echo -e "${GREEN}Migrations complete!${NC}"

# Start uvicorn with hot reload
echo -e "${GREEN}Starting control plane on http://localhost:8000${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
echo ""

cd "$PROJECT_ROOT/hosted/control"
uvicorn app:app --reload --host 0.0.0.0 --port 8000
