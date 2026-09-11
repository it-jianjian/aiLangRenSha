"""add game_reviews table for cached AI replay review insights.

Revision ID: 20260912_08
Revises: 20260911_07

复盘功能：缓存按需生成的 AI 复盘点评（MVP / 转折点 / 最佳最差操作 / 总评）。
胜率曲线为实时规则计算不入表。game_reviews 为新表，启动时 create_all 会自动创建；
本迁移做幂等建表，保证历史库通过 alembic upgrade head 也能补齐。
"""

import sqlalchemy as sa

from alembic import op

revision = "20260912_08"
down_revision = "20260911_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 幂等：create_all 可能已建好该表（基线+增量模式），已存在则跳过
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "game_reviews" in insp.get_table_names():
        return
    op.create_table(
        "game_reviews",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("game_id", sa.String(length=36), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("review_json", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_game_reviews_game_id", "game_reviews", ["game_id"], unique=True)


def downgrade() -> None:
    raise RuntimeError("game_reviews migration downgrade is intentionally unsupported; restore a verified backup instead")
