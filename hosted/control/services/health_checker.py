"""Background health checker for user PCP nodes.

Periodically checks the health of all running nodes and updates their status.
Logs failures and can trigger alerts after consecutive failures.
"""

import asyncio
import logging
from datetime import datetime, UTC

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session_factory
from ..models import Node, NodeStatus, HealthStatus
from .node_client import NodeClient

logger = logging.getLogger(__name__)


class HealthChecker:
    """Background service that monitors node health."""

    def __init__(
        self,
        check_interval: float = 60.0,
        failure_threshold: int = 3,
        timeout: float = 10.0,
    ):
        """Initialize the health checker.

        Args:
            check_interval: Seconds between health check cycles.
            failure_threshold: Consecutive failures before marking unhealthy.
            timeout: Timeout for individual health checks.
        """
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background health checker."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Health checker started")

    async def stop(self) -> None:
        """Stop the background health checker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Health checker stopped")

    async def _run_loop(self) -> None:
        """Main loop that periodically checks all nodes."""
        while self._running:
            try:
                await self._check_all_nodes()
            except Exception as e:
                logger.error(f"Health check cycle failed: {e}")

            await asyncio.sleep(self.check_interval)

    async def _check_all_nodes(self) -> None:
        """Check health of all running nodes."""
        factory = get_session_factory()

        async with factory() as db:
            # Get all running nodes
            result = await db.execute(
                select(Node).where(Node.status == NodeStatus.RUNNING)
            )
            nodes = result.scalars().all()

            if not nodes:
                return

            logger.debug(f"Checking health of {len(nodes)} nodes")

            # Check each node concurrently
            tasks = [self._check_node(db, node) for node in nodes]
            await asyncio.gather(*tasks, return_exceptions=True)

            await db.commit()

    async def _check_node(self, db: AsyncSession, node: Node) -> None:
        """Check health of a single node.

        Args:
            db: Database session.
            node: The node to check.
        """
        # Get username from container name (pcp-{username})
        if not node.container_name:
            return

        username = node.container_name.replace("pcp-", "")
        internal_url = f"http://{node.container_name}:9315"

        try:
            async with NodeClient(internal_url, timeout=self.timeout) as client:
                is_healthy = await client.health_check()

            if is_healthy:
                # Node is healthy
                if node.health_status != HealthStatus.HEALTHY:
                    logger.info(f"Node {username} is now healthy")

                node.health_status = HealthStatus.HEALTHY
                node.consecutive_failures = 0
                node.last_health_check = datetime.now(UTC)
            else:
                # Health check returned but not healthy
                await self._record_failure(node, username, "Health check returned unhealthy")

        except Exception as e:
            # Health check failed
            await self._record_failure(node, username, str(e))

    async def _record_failure(
        self,
        node: Node,
        username: str,
        reason: str,
    ) -> None:
        """Record a health check failure.

        Args:
            node: The node that failed.
            username: The node's username.
            reason: Reason for the failure.
        """
        node.consecutive_failures += 1
        node.last_health_check = datetime.now(UTC)

        if node.consecutive_failures >= self.failure_threshold:
            if node.health_status != HealthStatus.UNHEALTHY:
                logger.warning(
                    f"Node {username} marked unhealthy after {node.consecutive_failures} failures: {reason}"
                )
                # Could trigger alert here (email, webhook, etc.)

            node.health_status = HealthStatus.UNHEALTHY
        else:
            logger.debug(
                f"Node {username} health check failed ({node.consecutive_failures}/{self.failure_threshold}): {reason}"
            )

    async def check_single_node(self, node_id: str) -> HealthStatus:
        """Check health of a single node by ID.

        Args:
            node_id: The node's UUID.

        Returns:
            The updated health status.
        """
        factory = get_session_factory()

        async with factory() as db:
            result = await db.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()

            if not node:
                return HealthStatus.UNKNOWN

            await self._check_node(db, node)
            await db.commit()

            return node.health_status


# Global health checker instance
_health_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker:
    """Get the global health checker instance."""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


async def start_health_checker() -> None:
    """Start the global health checker."""
    checker = get_health_checker()
    await checker.start()


async def stop_health_checker() -> None:
    """Stop the global health checker."""
    global _health_checker
    if _health_checker:
        await _health_checker.stop()
        _health_checker = None
