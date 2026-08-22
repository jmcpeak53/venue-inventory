"""Create web_sessions for opaque server-side sessions.

Revision ID: 0001_web_sessions
Revises:
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_web_sessions"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_digest", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('admin', 'booking')",
            name="ck_web_sessions_actor_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_digest", name="uq_web_sessions_session_digest"),
    )
    op.create_index(
        "ix_web_sessions_expires_at",
        "web_sessions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_web_sessions_expires_at", table_name="web_sessions")
    op.drop_table("web_sessions")
