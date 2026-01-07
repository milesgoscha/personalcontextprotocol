"""Docker-based node provisioner.

Handles the complete lifecycle of user PCP nodes:
1. Create Docker volume for persistent data
2. Start container with Traefik labels for routing
3. Wait for health check
4. Obtain and encrypt admin token
5. Handle restarts and cleanup
"""

import asyncio
from enum import Enum
from typing import Any

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from ..config import get_settings


class ProvisioningError(Exception):
    """Raised when provisioning fails."""

    pass


class ContainerState(str, Enum):
    """Docker container states."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    RESTARTING = "restarting"
    REMOVING = "removing"
    EXITED = "exited"
    DEAD = "dead"


class Provisioner:
    """Docker-based node provisioner."""

    def __init__(self):
        """Initialize the provisioner with Docker client."""
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        """Get or create Docker client."""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _volume_name(self, username: str) -> str:
        """Generate volume name for a user."""
        return f"pcp-data-{username}"

    def _container_name(self, username: str) -> str:
        """Generate container name for a user."""
        return f"pcp-{username}"

    def _get_traefik_labels(self, username: str) -> dict[str, str]:
        """Generate Traefik labels for routing.

        Creates labels for:
        - Dev mode: HTTP only routing
        - Prod mode: HTTP to HTTPS redirect + TLS
        """
        settings = get_settings()
        domain = settings.pcp_domain
        router_name = f"pcp-{username}"

        # Dev mode: HTTP only (domains ending in .localhost)
        if domain.endswith(".localhost"):
            return {
                "traefik.enable": "true",
                f"traefik.http.routers.{router_name}.rule": f"Host(`{username}.{domain}`)",
                f"traefik.http.routers.{router_name}.entrypoints": "web",
                f"traefik.http.services.{router_name}.loadbalancer.server.port": "6001",
            }

        # Production mode: HTTPS with redirect
        return {
            # Enable Traefik
            "traefik.enable": "true",
            # HTTP router (redirect to HTTPS)
            f"traefik.http.routers.{router_name}-http.rule": f"Host(`{username}.{domain}`)",
            f"traefik.http.routers.{router_name}-http.entrypoints": "web",
            f"traefik.http.routers.{router_name}-http.middlewares": "redirect-to-https",
            # HTTPS router
            f"traefik.http.routers.{router_name}.rule": f"Host(`{username}.{domain}`)",
            f"traefik.http.routers.{router_name}.entrypoints": "websecure",
            f"traefik.http.routers.{router_name}.tls": "true",
            f"traefik.http.routers.{router_name}.tls.certresolver": "letsencrypt",
            # Service port
            f"traefik.http.services.{router_name}.loadbalancer.server.port": "6001",
        }

    async def create_volume(self, username: str) -> str:
        """Create a Docker volume for the user's data.

        Args:
            username: The user's username.

        Returns:
            The volume name.

        Raises:
            ProvisioningError: If volume creation fails.
        """
        volume_name = self._volume_name(username)

        try:
            # Check if volume already exists
            try:
                self.client.volumes.get(volume_name)
                return volume_name  # Already exists
            except NotFound:
                pass

            # Create new volume
            self.client.volumes.create(
                name=volume_name,
                labels={
                    "pcp.user": username,
                    "pcp.managed": "true",
                },
            )
            return volume_name
        except APIError as e:
            raise ProvisioningError(f"Failed to create volume: {e}")

    async def start_container(
        self,
        username: str,
        node_id: str,
    ) -> tuple[str, str]:
        """Start a PCP container for the user.

        Args:
            username: The user's username.
            node_id: The PCP node ID (pcp://{username}).

        Returns:
            Tuple of (container_id, internal_url).

        Raises:
            ProvisioningError: If container start fails.
        """
        settings = get_settings()
        container_name = self._container_name(username)
        volume_name = self._volume_name(username)

        try:
            # Check if container already exists
            try:
                existing = self.client.containers.get(container_name)
                if existing.status == "running":
                    internal_url = f"http://{container_name}:6001"
                    return existing.id, internal_url
                # Remove stopped container
                existing.remove(force=True)
            except NotFound:
                pass

            # Start new container
            public_url = f"https://{username}.{settings.pcp_domain}"
            container: Container = self.client.containers.run(
                image=settings.pcp_image,
                name=container_name,
                detach=True,
                environment={
                    "PCP_NODE_ID": node_id,
                    "PCP_PUBLIC_URL": public_url,
                    # Allow initial token request from control plane
                    "PCP_ALLOW_INITIAL_TOKEN": "true",
                },
                volumes={
                    volume_name: {"bind": "/data", "mode": "rw"},
                },
                labels={
                    **self._get_traefik_labels(username),
                    "pcp.user": username,
                    "pcp.managed": "true",
                },
                network=settings.docker_network,
                restart_policy={"Name": "unless-stopped"},
                # Resource limits
                mem_limit="512m",
                cpu_period=100000,
                cpu_quota=50000,  # 0.5 CPU
            )

            internal_url = f"http://{container_name}:6001"
            return container.id, internal_url

        except APIError as e:
            raise ProvisioningError(f"Failed to start container: {e}")

    async def wait_for_healthy(
        self,
        internal_url: str,
        timeout: float = 60.0,
        interval: float = 2.0,
    ) -> bool:
        """Wait for a container to become healthy.

        Args:
            internal_url: The container's internal URL.
            timeout: Maximum time to wait in seconds.
            interval: Check interval in seconds.

        Returns:
            True if healthy, False if timeout.
        """
        import httpx

        start_time = asyncio.get_event_loop().time()

        async with httpx.AsyncClient(timeout=5.0) as client:
            while asyncio.get_event_loop().time() - start_time < timeout:
                try:
                    response = await client.get(f"{internal_url}/health")
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == "healthy":
                            return True
                except httpx.RequestError:
                    pass

                await asyncio.sleep(interval)

        return False

    async def stop_container(self, username: str) -> None:
        """Stop a user's container.

        Args:
            username: The user's username.
        """
        container_name = self._container_name(username)

        try:
            container = self.client.containers.get(container_name)
            container.stop(timeout=10)
        except NotFound:
            pass  # Already stopped/removed
        except APIError as e:
            raise ProvisioningError(f"Failed to stop container: {e}")

    async def restart_container(self, username: str) -> None:
        """Restart a user's container.

        Args:
            username: The user's username.
        """
        container_name = self._container_name(username)

        try:
            container = self.client.containers.get(container_name)
            container.restart(timeout=10)
        except NotFound:
            raise ProvisioningError(f"Container {container_name} not found")
        except APIError as e:
            raise ProvisioningError(f"Failed to restart container: {e}")

    async def remove_container(self, username: str) -> None:
        """Remove a user's container (not the volume).

        Args:
            username: The user's username.
        """
        container_name = self._container_name(username)

        try:
            container = self.client.containers.get(container_name)
            container.remove(force=True)
        except NotFound:
            pass
        except APIError as e:
            raise ProvisioningError(f"Failed to remove container: {e}")

    async def remove_volume(self, username: str) -> None:
        """Remove a user's data volume.

        WARNING: This permanently deletes all user data!

        Args:
            username: The user's username.
        """
        volume_name = self._volume_name(username)

        try:
            volume = self.client.volumes.get(volume_name)
            volume.remove(force=True)
        except NotFound:
            pass
        except APIError as e:
            raise ProvisioningError(f"Failed to remove volume: {e}")

    async def cleanup_user(self, username: str) -> None:
        """Completely clean up all resources for a user.

        This removes:
        - Container (forced)
        - Volume (with all data)

        Args:
            username: The user's username.
        """
        await self.remove_container(username)
        await self.remove_volume(username)

    def get_container_status(self, username: str) -> ContainerState | None:
        """Get the current status of a user's container.

        Args:
            username: The user's username.

        Returns:
            ContainerState or None if not found.
        """
        container_name = self._container_name(username)

        try:
            container = self.client.containers.get(container_name)
            return ContainerState(container.status)
        except NotFound:
            return None
        except APIError:
            return None

    def get_container_logs(
        self,
        username: str,
        tail: int = 100,
    ) -> str:
        """Get recent logs from a user's container.

        Args:
            username: The user's username.
            tail: Number of lines to return.

        Returns:
            Log output as string.
        """
        container_name = self._container_name(username)

        try:
            container = self.client.containers.get(container_name)
            logs = container.logs(tail=tail, timestamps=True)
            return logs.decode("utf-8", errors="replace")
        except NotFound:
            return ""
        except APIError:
            return ""


# Global provisioner instance
_provisioner: Provisioner | None = None


def get_provisioner() -> Provisioner:
    """Get the global provisioner instance."""
    global _provisioner
    if _provisioner is None:
        _provisioner = Provisioner()
    return _provisioner
