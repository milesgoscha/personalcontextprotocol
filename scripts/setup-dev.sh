#!/bin/bash
# First-time setup for PCP local development
# Run this once to install dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Setting up PCP local development environment...${NC}"

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
if [[ $(echo "$PYTHON_VERSION < 3.11" | bc -l) -eq 1 ]]; then
    echo -e "${YELLOW}Warning: Python 3.11+ recommended. You have $PYTHON_VERSION${NC}"
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$PROJECT_ROOT/.venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv "$PROJECT_ROOT/.venv"
fi

# Activate virtual environment
source "$PROJECT_ROOT/.venv/bin/activate"

# Install dependencies
echo -e "${YELLOW}Installing PCP node package...${NC}"
pip install -e "$PROJECT_ROOT"

echo -e "${YELLOW}Installing hosted control plane package...${NC}"
pip install -e "$PROJECT_ROOT/hosted"

echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "To start developing:"
echo "  1. Activate the venv:  source .venv/bin/activate"
echo "  2. Run dev server:     ./scripts/dev.sh"
echo ""
echo "The dev server will:"
echo "  - Start Postgres in Docker (if not running)"
echo "  - Run database migrations"
echo "  - Start uvicorn with hot reload on http://localhost:8000"
