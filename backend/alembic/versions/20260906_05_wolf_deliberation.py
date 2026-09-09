"""add agent_logs.deliberation_round for werewolf deliberation tracking.

Revision ID: 20260906_05
Revises: 20260813_04

需求一（狼队协商）FR-5 埋点：区分每狼两轮表态（1=首表态 / 2=修订或坚持），
支持复盘"谁被谁说服了"。非协商决策该列为 NULL。

备注：GameRound.werewolf_agreement(String16) 为历史死列（全仓无写入点），
agreement 真源在 night_kill 事件 JSON，本次不写该列、不改其宽度。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260906_05"
down_revision = "20260813_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_logs") as batch:
        batch.add_column(sa.Column("deliberation_round", sa.Integer, nullable=True))


def downgrade() -> None:
    raise RuntimeError("wolf deliberation migration downgrade is intentionally unsupported; restore a verified backup instead")
