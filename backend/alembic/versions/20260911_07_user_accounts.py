"""add user-account linkage columns: games.owner_user_id, game_players.user_id.

Revision ID: 20260911_07
Revises: 20260906_06

可选登录：把对局与创建者/人类座位绑定到用户账号，支撑「我的对局/战绩」。
users / custom_models 为新表，由启动时 create_all 自动创建，无需迁移。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260911_07"
down_revision = "20260906_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 幂等：本项目启动时 create_all 可能已先建好这些列（基线+增量模式），已存在则跳过
    bind = op.get_bind()
    insp = sa.inspect(bind)
    games_cols = {c["name"] for c in insp.get_columns("games")}
    players_cols = {c["name"] for c in insp.get_columns("game_players")}
    with op.batch_alter_table("games") as batch:
        if "owner_user_id" not in games_cols:
            batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("game_players") as batch:
        if "user_id" not in players_cols:
            batch.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    raise RuntimeError("user accounts migration downgrade is intentionally unsupported; restore a verified backup instead")
