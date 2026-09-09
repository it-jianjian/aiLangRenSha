"""add agent_logs.critique_result / revised for speech critique loop tracking.

Revision ID: 20260906_06
Revises: 20260906_05

需求二（发言批评-修订）FR-4 埋点：记录每条被审稿发言的评审单摘要
（risk_level、issue 类型列表、fix_hint）与是否触发修订，支持观测
"哪类穿帮最高频"。非批评路径两列为 NULL。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260906_06"
down_revision = "20260906_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_logs") as batch:
        batch.add_column(sa.Column("critique_result", sa.Text(), nullable=True))
        batch.add_column(sa.Column("revised", sa.Boolean(), nullable=True))


def downgrade() -> None:
    raise RuntimeError("speech critique migration downgrade is intentionally unsupported; restore a verified backup instead")
