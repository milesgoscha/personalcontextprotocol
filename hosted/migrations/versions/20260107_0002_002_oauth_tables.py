"""Add OAuth 2.1 tables for dynamic client registration and token management.

Revision ID: 002
Revises: 001
Create Date: 2026-01-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create OAuth clients table
    op.create_table(
        "oauth_clients",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=False),
        sa.Column("redirect_uris", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id"),
    )

    # Create OAuth authorization codes table
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("username", sa.String(63), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )

    # Create OAuth refresh tokens table
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("username", sa.String(63), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("pcp_token_id", sa.String(255), nullable=False),
        sa.Column("pcp_grant_id", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["oauth_clients.client_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
    )

    # Create indexes for common queries
    op.create_index(
        "ix_oauth_authorization_codes_expires_at",
        "oauth_authorization_codes",
        ["expires_at"],
    )
    op.create_index(
        "ix_oauth_refresh_tokens_expires_at",
        "oauth_refresh_tokens",
        ["expires_at"],
    )
    op.create_index(
        "ix_oauth_refresh_tokens_user_id",
        "oauth_refresh_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_refresh_tokens_user_id", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_expires_at", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_authorization_codes_expires_at", table_name="oauth_authorization_codes")
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
