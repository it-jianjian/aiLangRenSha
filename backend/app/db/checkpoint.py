"""AI 狼人杀 — LangGraph 检查点存储适配

按 settings.database_url 的 scheme 选择检查点后端，使 ORM 主库与 LangGraph 检查点
落到同一种数据库：
- mysql*        → AIOMySQLSaver（langgraph-checkpoint-mysql；检查点表首次使用时自动建）
- 其他（sqlite）→ AsyncSqliteSaver（data/werewolf.db）

因此 .env 把 DATABASE_URL 切到 MySQL 时，检查点也随之落到 MySQL；无 .env（默认 SQLite）
时保持原行为，测试与本地免配置开发不受影响。

调用链：game_flow.start_game / resume_game、main.lifespan → get_checkpointer()
"""

import logging
from contextlib import asynccontextmanager

from app.config import get_settings

logger = logging.getLogger(__name__)

# 检查点表建一次即可（进程内），避免每局重复 DDL
_mysql_setup_done = False


@asynccontextmanager
async def get_checkpointer():
    """产出与当前 database_url 匹配的 LangGraph 检查点 saver（异步上下文管理器）。"""
    global _mysql_setup_done
    settings = get_settings()
    url = settings.database_url

    if url.startswith("mysql"):
        from langgraph.checkpoint.mysql.aio import AIOMySQLSaver

        async with AIOMySQLSaver.from_conn_string(url) as saver:
            if not _mysql_setup_done:
                await saver.setup()  # CREATE TABLE IF NOT EXISTS，幂等
                _mysql_setup_done = True
                logger.info("[Checkpoint] MySQL 检查点表已就绪")
            yield saver
    else:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        db_path = settings.data_dir / "werewolf.db"
        async with AsyncSqliteSaver.from_conn_string(str(db_path).replace("\\", "/")) as saver:
            yield saver
