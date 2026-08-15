"""add agent_steps table for ReAct agent trajectory tracking.

Revision ID: 20260813_04
Revises: 20260813_03
"""

import sqlalchemy as sa

from alembic import op

revision = "20260813_04"
down_revision = "20260813_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_log_id", sa.String(36), sa.ForeignKey("agent_logs.id"), nullable=False),
        sa.Column("game_id", sa.String(36), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("step_type", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_agent_steps_game_log", "agent_steps", ["game_id", "agent_log_id"])


def downgrade() -> None:
    raise RuntimeError("agent_steps migration downgrade is intentionally unsupported; restore a verified backup instead")
