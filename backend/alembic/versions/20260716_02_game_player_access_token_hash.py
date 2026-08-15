"""add access token hash to game players.

Revision ID: 20260716_02
Revises: 20260716_01
"""

import sqlalchemy as sa

from alembic import op

revision = "20260716_02"
down_revision = "20260716_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("game_players") as batch:
        batch.add_column(sa.Column("access_token_hash", sa.String(128), nullable=True))


def downgrade() -> None:
    # 禁止生产回退执行破坏性 DROP；由运维从迁移前备份恢复。
    raise RuntimeError("access token hash migration downgrade is intentionally unsupported; restore a verified backup instead")
