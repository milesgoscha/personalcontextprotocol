# PCP Node Server Dockerfile
#
# Build:   docker build -t pcp-node .
# Run:     docker run -p 6001:6001 -v pcp-data:/data pcp-node

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python package
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

# Create data directory
RUN mkdir -p /data

# Environment variables
ENV PCP_DATA_DIR=/data
ENV PCP_HOST=0.0.0.0
ENV PCP_PORT=6001

# Expose port
EXPOSE 6001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:6001/health')" || exit 1

# Run the server
CMD ["python", "-m", "uvicorn", "pcp.server.app:app", "--host", "0.0.0.0", "--port", "6001"]
