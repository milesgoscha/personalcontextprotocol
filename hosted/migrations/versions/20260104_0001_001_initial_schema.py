"""Initial schema for PCP Hosted Service.

Revision ID: 001
Revises:
Create Date: 2026-01-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types (IF NOT EXISTS for idempotency)
    op.execute("DO $$ BEGIN CREATE TYPE nodestatus AS ENUM ('pending', 'provisioning', 'running', 'stopped', 'error'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE healthstatus AS ENUM ('healthy', 'unhealthy', 'unknown'); EXCEPTION WHEN duplicate_object THEN null; END $$;")

    # Create users table
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("username", sa.String(63), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("username"),
    )

    # Create nodes table
    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("container_id", sa.String(64), nullable=True),
        sa.Column("container_name", sa.String(255), nullable=True),
        sa.Column("volume_name", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "provisioning", "running", "stopped", "error", name="nodestatus", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("admin_token_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("admin_token_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("public_url", sa.String(255), nullable=True),
        sa.Column("node_id", sa.String(255), nullable=True),
        sa.Column(
            "health_status",
            sa.Enum("healthy", "unhealthy", "unknown", name="healthstatus", create_type=False),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_health_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id"),
    )

    # Create sessions table
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("refresh_token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )

    # Create indexes for common queries
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_nodes_status", "nodes", ["status"])
    op.create_index("ix_nodes_health_status", "nodes", ["health_status"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_audit_logs_created_at", "audit_logs")
    op.drop_index("ix_audit_logs_user_id", "audit_logs")
    op.drop_index("ix_sessions_expires_at", "sessions")
    op.drop_index("ix_sessions_user_id", "sessions")
    op.drop_index("ix_nodes_health_status", "nodes")
    op.drop_index("ix_nodes_status", "nodes")
    op.drop_index("ix_users_username", "users")
    op.drop_index("ix_users_email", "users")

    # Drop tables
    op.drop_table("audit_logs")
    op.drop_table("sessions")
    op.drop_table("nodes")
    op.drop_table("users")

    # Drop enum types
    op.execute("DROP TYPE healthstatus")
    op.execute("DROP TYPE nodestatus")
