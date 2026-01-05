"""HTTP client for communicating with user PCP nodes.

This client is used by the control plane to:
1. Check node health
2. Fetch admin token during provisioning
3. Proxy dashboard requests (grants, audit, tokens)
"""

from typing import Any

import httpx

from ..config import get_settings


class NodeClientError(Exception):
    """Base exception for node client errors."""

    pass


class NodeUnreachableError(NodeClientError):
    """Raised when the node cannot be reached."""

    pass


class NodeAuthError(NodeClientError):
    """Raised when authentication with the node fails."""

    pass


class NodeClient:
    """HTTP client for a user's PCP node."""

    def __init__(
        self,
        base_url: str,
        admin_token: str | None = None,
        timeout: float = 10.0,
    ):
        """Initialize the node client.

        Args:
            base_url: The node's base URL (e.g., http://pcp-alice:6001)
            admin_token: Optional admin token for authenticated requests.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NodeClient":
        """Enter async context."""
        headers = {}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get the HTTP client, ensuring it's initialized."""
        if self._client is None:
            raise RuntimeError("NodeClient must be used as async context manager")
        return self._client

    async def health_check(self) -> bool:
        """Check if the node is healthy.

        Returns:
            True if the node responds with healthy status.
        """
        try:
            response = await self.client.get("/health")
            if response.status_code == 200:
                data = response.json()
                return data.get("status") == "healthy"
            return False
        except httpx.RequestError:
            return False

    async def get_admin_token(self) -> str:
        """Request an admin token from a freshly provisioned node.

        This is called once during provisioning to get the initial admin token.
        The node must be configured to allow this initial token request.

        Returns:
            The admin token string.

        Raises:
            NodeClientError: If the request fails.
        """
        try:
            # Request admin token with full permissions
            response = await self.client.post(
                "/api/token",
                json={
                    "subject": "control-plane",
                    "scopes": ["admin"],
                    "hours": 87600,  # 10 years - effectively permanent
                },
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("token")
            elif response.status_code == 401:
                raise NodeAuthError("Unauthorized to request admin token")
            else:
                raise NodeClientError(
                    f"Failed to get admin token: {response.status_code} {response.text}"
                )
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def get_grants(self) -> list[dict[str, Any]]:
        """Get all pending and active grants.

        Returns:
            List of grant objects.
        """
        try:
            response = await self.client.get("/api/grants")
            response.raise_for_status()
            data = response.json()
            return data.get("grants", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            raise NodeClientError(f"Failed to get grants: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def approve_grant(self, grant_id: str) -> dict[str, Any]:
        """Approve a pending grant.

        Args:
            grant_id: The grant UUID.

        Returns:
            The updated grant object.
        """
        try:
            response = await self.client.post(f"/api/grants/{grant_id}/approve")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            if e.response.status_code == 404:
                raise NodeClientError(f"Grant {grant_id} not found")
            raise NodeClientError(f"Failed to approve grant: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def deny_grant(self, grant_id: str) -> dict[str, Any]:
        """Deny a pending grant.

        Args:
            grant_id: The grant UUID.

        Returns:
            The updated grant object.
        """
        try:
            response = await self.client.post(f"/api/grants/{grant_id}/deny")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            if e.response.status_code == 404:
                raise NodeClientError(f"Grant {grant_id} not found")
            raise NodeClientError(f"Failed to deny grant: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def revoke_grant(self, grant_id: str) -> dict[str, Any]:
        """Revoke an active grant.

        Args:
            grant_id: The grant UUID.

        Returns:
            The updated grant object.
        """
        try:
            response = await self.client.post(f"/api/grants/{grant_id}/revoke")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            if e.response.status_code == 404:
                raise NodeClientError(f"Grant {grant_id} not found")
            raise NodeClientError(f"Failed to revoke grant: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def create_token(
        self,
        subject: str,
        scopes: list[str],
        hours: int = 24,
    ) -> str:
        """Create a new token for the node.

        Args:
            subject: Token subject/name.
            scopes: List of permission scopes.
            hours: Token validity in hours.

        Returns:
            The token string.
        """
        try:
            response = await self.client.post(
                "/api/token",
                json={
                    "subject": subject,
                    "scopes": scopes,
                    "hours": hours,
                },
            )
            response.raise_for_status()
            return response.json().get("token")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            raise NodeClientError(f"Failed to create token: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def get_audit_log(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get audit log entries.

        Args:
            limit: Maximum entries to return.
            offset: Number of entries to skip.

        Returns:
            List of audit log entries.
        """
        try:
            response = await self.client.get(
                "/api/audit",
                params={"limit": limit, "offset": offset},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            raise NodeClientError(f"Failed to get audit log: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")

    async def export_data(self) -> bytes:
        """Export all node data as JSONL.

        Returns:
            Raw JSONL bytes.
        """
        try:
            response = await self.client.get("/api/export")
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise NodeAuthError("Admin token invalid or expired")
            raise NodeClientError(f"Failed to export data: {e}")
        except httpx.RequestError as e:
            raise NodeUnreachableError(f"Cannot reach node: {e}")
