"""
PCP Hosted Service Configuration.

Environment-based configuration using Pydantic Settings.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://pcp:pcp@localhost:5432/pcp_hosted"

    # JWT
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"  # Must be overridden
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Encryption (for admin tokens)
    master_encryption_key: str = "CHANGE_ME_IN_PRODUCTION"  # Must be overridden
    # Format: comma-separated list of versioned keys for rotation
    # e.g., "v1:key1,v2:key2" - newest version is used for encryption
    encryption_keys: str = ""

    # PCP Domain
    pcp_domain: str = "pcp.example.com"
    pcp_image: str = "pcp:latest"

    # Multi-tenant mode
    # When enabled, routes all requests to a shared PCP node with X-User-Id header
    # When disabled, creates per-user Docker containers (legacy mode)
    multi_tenant: bool = True
    shared_node_url: str = "http://pcp-node:6001"

    # Docker (only used when multi_tenant=False)
    docker_network: str = "pcp-network"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Rate limiting
    signup_rate_limit: int = 5  # per hour per IP
    max_nodes_per_host: int = 50

    # Admin users (comma-separated usernames)
    admin_usernames: str = ""

    def is_admin(self, username: str) -> bool:
        """Check if a username has admin access."""
        if not self.admin_usernames:
            return False
        admins = [u.strip().lower() for u in self.admin_usernames.split(",") if u.strip()]
        return username.lower() in admins

    @property
    def current_key_version(self) -> int:
        """Get the current (latest) encryption key version."""
        if not self.encryption_keys:
            return 1
        keys = self.encryption_keys.split(",")
        versions = [int(k.split(":")[0].lstrip("v")) for k in keys if ":" in k]
        return max(versions) if versions else 1

    def get_encryption_key(self, version: int | None = None) -> bytes:
        """
        Get encryption key by version.

        If version is None, returns the current (latest) key.
        Falls back to master_encryption_key if no versioned keys configured.
        """
        if not self.encryption_keys:
            # Fallback to master key (version 1)
            return self.master_encryption_key.encode()

        keys_map: dict[int, str] = {}
        for entry in self.encryption_keys.split(","):
            if ":" in entry:
                v, key = entry.split(":", 1)
                keys_map[int(v.lstrip("v"))] = key

        target_version = version if version is not None else self.current_key_version

        if target_version in keys_map:
            return keys_map[target_version].encode()

        # Fallback
        return self.master_encryption_key.encode()


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
