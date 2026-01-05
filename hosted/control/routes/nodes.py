"""Node management routes.

Handles node provisioning, status, restart, and deletion.
"""

import asyncio
from datetime import datetime, UTC
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.middleware import CurrentUser
from ..config import get_settings
from ..database import get_db
from ..models import Node, NodeStatus, HealthStatus, AuditLog
from ..services.encryption import encrypt_token
from ..services.node_client import NodeClient, NodeClientError
from ..services.provisioner import get_provisioner, ProvisioningError

router = APIRouter()


# --- Response Models ---


class NodeResponse(BaseModel):
    """Response containing node info."""

    id: str
    status: str
    health_status: str
    public_url: str | None
    node_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class NodeLogsResponse(BaseModel):
    """Response containing node logs."""

    logs: str


# --- Helper Functions ---


async def _get_user_node(db: AsyncSession, user_id: str) -> Node | None:
    """Get a user's node from the database."""
    result = await db.execute(select(Node).where(Node.user_id == user_id))
    return result.scalar_one_or_none()


async def _log_audit(
    db: AsyncSession,
    user_id: str,
    action: str,
    details: str | None = None,
) -> None:
    """Create an audit log entry."""
    log = AuditLog(
        id=str(uuid4()),
        user_id=user_id,
        action=action,
        details=details,
    )
    db.add(log)


async def _provision_node_task(
    node_id: str,
    user_id: str,
    username: str,
) -> None:
    """Background task to provision a node.

    This runs asynchronously after the initial node record is created.
    Updates the node status as it progresses through provisioning.
    """
    from ..database import get_session_factory

    settings = get_settings()
    provisioner = get_provisioner()
    factory = get_session_factory()

    async with factory() as db:
        # Get the node
        result = await db.execute(select(Node).where(Node.id == node_id))
        node = result.scalar_one_or_none()

        if not node:
            return

        try:
            # Update status to provisioning
            node.status = NodeStatus.PROVISIONING
            await db.commit()

            # Create volume
            await provisioner.create_volume(username)

            # Generate node ID and public URL
            pcp_node_id = f"pcp://{username}"
            public_url = f"https://{username}.{settings.pcp_domain}"

            # Start container
            container_id, internal_url = await provisioner.start_container(
                username=username,
                node_id=pcp_node_id,
            )

            # Update node with container info
            node.container_id = container_id
            node.container_name = f"pcp-{username}"
            node.volume_name = f"pcp-data-{username}"
            node.node_id = pcp_node_id
            node.public_url = public_url
            await db.commit()

            # Wait for container to be healthy
            healthy = await provisioner.wait_for_healthy(internal_url, timeout=60)

            if not healthy:
                node.status = NodeStatus.ERROR
                node.error_message = "Container failed to become healthy"
                node.health_status = HealthStatus.UNHEALTHY
                await db.commit()
                return

            # Get admin token from the node
            async with NodeClient(internal_url) as client:
                admin_token = await client.get_admin_token()

            # Encrypt and store admin token
            encrypted = encrypt_token(admin_token, user_id)
            node.admin_token_encrypted = encrypted.ciphertext
            node.admin_token_version = encrypted.key_version

            # Mark as running
            node.status = NodeStatus.RUNNING
            node.health_status = HealthStatus.HEALTHY
            node.last_health_check = datetime.now(UTC)
            node.error_message = None
            await db.commit()

            # Audit log
            await _log_audit(
                db,
                user_id,
                "node_provisioned",
                f"Node {pcp_node_id} provisioned successfully",
            )
            await db.commit()

        except ProvisioningError as e:
            node.status = NodeStatus.ERROR
            node.error_message = str(e)
            await db.commit()

        except NodeClientError as e:
            node.status = NodeStatus.ERROR
            node.error_message = f"Failed to get admin token: {e}"
            await db.commit()

        except Exception as e:
            node.status = NodeStatus.ERROR
            node.error_message = f"Unexpected error: {e}"
            await db.commit()


# --- Routes ---


@router.get("", response_model=NodeResponse)
async def get_node(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NodeResponse:
    """Get the current user's node status."""
    node = await _get_user_node(db, current_user.id)

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No node found. Node will be created on signup.",
        )

    return NodeResponse(
        id=node.id,
        status=node.status.value,
        health_status=node.health_status.value,
        public_url=node.public_url,
        node_id=node.node_id,
        error_message=node.error_message,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.post("/provision", response_model=NodeResponse, status_code=status.HTTP_202_ACCEPTED)
async def provision_node(
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NodeResponse:
    """Provision a new node for the user.

    This is called automatically after signup, but can also be called
    manually to retry failed provisioning.
    """
    # Check if node already exists
    node = await _get_user_node(db, current_user.id)

    if node:
        if node.status == NodeStatus.RUNNING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Node already running",
            )
        elif node.status == NodeStatus.PROVISIONING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Node is already being provisioned",
            )
        # Allow retry for pending or error states
    else:
        # Create new node record
        node = Node(
            id=str(uuid4()),
            user_id=current_user.id,
            status=NodeStatus.PENDING,
        )
        db.add(node)
        await db.flush()

    # Reset error state if retrying
    if node.status == NodeStatus.ERROR:
        node.status = NodeStatus.PENDING
        node.error_message = None

    await db.commit()

    # Start provisioning in background
    background_tasks.add_task(
        _provision_node_task,
        node.id,
        current_user.id,
        current_user.username,
    )

    return NodeResponse(
        id=node.id,
        status=node.status.value,
        health_status=node.health_status.value,
        public_url=node.public_url,
        node_id=node.node_id,
        error_message=node.error_message,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.post("/restart", response_model=NodeResponse)
async def restart_node(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NodeResponse:
    """Restart the user's node."""
    node = await _get_user_node(db, current_user.id)

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No node found",
        )

    if node.status not in (NodeStatus.RUNNING, NodeStatus.STOPPED, NodeStatus.ERROR):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot restart node in {node.status.value} state",
        )

    try:
        provisioner = get_provisioner()
        await provisioner.restart_container(current_user.username)

        node.status = NodeStatus.RUNNING
        node.error_message = None
        node.updated_at = datetime.now(UTC)
        await db.commit()

        await _log_audit(db, current_user.id, "node_restarted")
        await db.commit()

    except ProvisioningError as e:
        node.status = NodeStatus.ERROR
        node.error_message = str(e)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    return NodeResponse(
        id=node.id,
        status=node.status.value,
        health_status=node.health_status.value,
        public_url=node.public_url,
        node_id=node.node_id,
        error_message=node.error_message,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.post("/stop", response_model=NodeResponse)
async def stop_node(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NodeResponse:
    """Stop the user's node."""
    node = await _get_user_node(db, current_user.id)

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No node found",
        )

    if node.status != NodeStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Node is not running (status: {node.status.value})",
        )

    try:
        provisioner = get_provisioner()
        await provisioner.stop_container(current_user.username)

        node.status = NodeStatus.STOPPED
        node.health_status = HealthStatus.UNKNOWN
        node.updated_at = datetime.now(UTC)
        await db.commit()

        await _log_audit(db, current_user.id, "node_stopped")
        await db.commit()

    except ProvisioningError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    return NodeResponse(
        id=node.id,
        status=node.status.value,
        health_status=node.health_status.value,
        public_url=node.public_url,
        node_id=node.node_id,
        error_message=node.error_message,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


class DeleteNodeRequest(BaseModel):
    """Request to delete a node (requires password confirmation)."""

    password: str


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    request: DeleteNodeRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete the user's node and all data.

    Requires password confirmation. This action is irreversible.
    """
    from ..auth.password import verify_password

    # Verify password
    if not verify_password(request.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    node = await _get_user_node(db, current_user.id)

    if not node:
        return  # No node to delete

    try:
        # Clean up Docker resources
        provisioner = get_provisioner()
        await provisioner.cleanup_user(current_user.username)

        # Delete node record
        await db.delete(node)
        await db.commit()

        await _log_audit(
            db,
            current_user.id,
            "node_deleted",
            "Node and all data permanently deleted",
        )
        await db.commit()

    except ProvisioningError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clean up resources: {e}",
        )


@router.get("/logs", response_model=NodeLogsResponse)
async def get_node_logs(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    tail: int = 100,
) -> NodeLogsResponse:
    """Get recent logs from the user's node."""
    node = await _get_user_node(db, current_user.id)

    if not node:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No node found",
        )

    provisioner = get_provisioner()
    logs = provisioner.get_container_logs(current_user.username, tail=tail)

    return NodeLogsResponse(logs=logs)
