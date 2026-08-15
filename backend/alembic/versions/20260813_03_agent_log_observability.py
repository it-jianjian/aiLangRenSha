"""add token and model observability fields to agent_logs.

Revision ID: 20260813_03
Revises: 20260716_02
"""

import sqlalchemy as sa

from alembic import op

revision = "20260813_03"
down_revision = "20260716_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_logs") as batch:
        batch.add_column(sa.Column("prompt_tokens", sa.Integer, nullable=True))
        batch.add_column(sa.Column("completion_tokens", sa.Integer, nullable=True))
        batch.add_column(sa.Column("model_name", sa.String(64), nullable=True))
        # Phase 0 D1: indexes for analysis queries
        batch.create_index("idx_agent_logs_created_at", ["created_at"])
        batch.create_index("idx_agent_logs_action_type", ["action_type"])


def downgrade() -> None:
    raise RuntimeError("agent_log observability migration downgrade is intentionally unsupported; restore a verified backup instead")
