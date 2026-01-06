"""
PCP Server Configuration.

Centralizes environment-based configuration for the PCP server.
Supports both single-tenant (self-hosted) and multi-tenant (hosted) modes.
"""

import os
from pathlib import Path


class Config:
    """Server configuration loaded from environment variables."""

    # Multi-tenant mode: when enabled, requires X-User-Id header and scopes
    # all storage to per-user directories
    MULTI_TENANT: bool = os.getenv("PCP_MULTI_TENANT", "false").lower() == "true"

    # Data directory for persistent storage
    # Default to ~/.pcp/data for local development, /data in Docker (via env var)
    DATA_DIR: Path = Path(os.getenv("PCP_DATA_DIR", str(Path.home() / ".pcp" / "data")))

    # Node identifier (e.g., "pcp://miles")
    NODE_ID: str = os.getenv("PCP_NODE_ID", "pcp://localhost")

    # JWT secret for token signing (should be set in production)
    JWT_SECRET: str = os.getenv("PCP_JWT_SECRET", "dev-secret-change-me")

    # Allow initial token creation without authentication (dev only)
    ALLOW_INITIAL_TOKEN: bool = os.getenv("PCP_ALLOW_INITIAL_TOKEN", "false").lower() == "true"

    # Anthropic API key for reflect operation
    ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")

    @classmethod
    def validate(cls) -> list[str]:
        """Validate configuration and return list of warnings."""
        warnings = []

        if cls.JWT_SECRET == "dev-secret-change-me":
            warnings.append("Using default JWT secret - set PCP_JWT_SECRET in production")

        if cls.MULTI_TENANT and cls.ALLOW_INITIAL_TOKEN:
            warnings.append("ALLOW_INITIAL_TOKEN should be disabled in multi-tenant mode")

        if not cls.ANTHROPIC_API_KEY:
            warnings.append("ANTHROPIC_API_KEY not set - reflect operation will fail")

        return warnings
