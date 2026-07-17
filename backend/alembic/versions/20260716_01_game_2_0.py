"""game 2.0 configuration and replay fields.

Revision ID: 20260716_01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260716_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("player_count", sa.Integer(), nullable=False, server_default="6"))
        batch.add_column(sa.Column("roster_type", sa.String(16), nullable=False, server_default="official"))
        batch.add_column(sa.Column("roster_json", sa.Text(), nullable=False, server_default='{"werewolf":2,"villager":2,"seer":1,"witch":1,"hunter":0,"guard":0}'))
        batch.add_column(sa.Column("roster_locked_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("owner_token_hash", sa.String(128), nullable=True))
    with op.batch_alter_table("game_rounds") as batch:
        batch.add_column(sa.Column("guard_target_seat", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("hunter_shot_seat", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("hunter_shot_trigger", sa.String(32), nullable=True))
    op.create_index("idx_games_status_created_at", "games", ["status", "created_at"])
    op.create_index("idx_game_events_game_round_created", "game_events", ["game_id", "round_number", "created_at"])
    # 使用 SQLite 字符串拼接，避免 Alembic 将 JSON 中的 :2 解析成绑定参数。
    op.execute("UPDATE games SET player_count = 6, roster_type = 'official', roster_json = "
               "'{\"werewolf\"' || ':' || '2,\"villager\"' || ':' || '2,\"seer\"' || ':' || "
               "'1,\"witch\"' || ':' || '1,\"hunter\"' || ':' || '0,\"guard\"' || ':' || '0}'")
    op.execute("UPDATE games SET roster_locked_at = COALESCE(started_at, CURRENT_TIMESTAMP) WHERE status IN ('playing', 'finished')")


def downgrade() -> None:
    # 禁止生产回退执行破坏性 DROP；由运维从迁移前备份恢复。
    raise RuntimeError("2.0 migration downgrade is intentionally unsupported; restore a verified backup instead")
