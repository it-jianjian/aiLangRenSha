"""受控地将应用数据库升级到 Alembic head。"""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from app.db.session import Base


def _schema_matches_metadata(engine) -> bool:
    """判断当前库是否已经包含 ORM 所需的全部表和字段。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in table_names:
            return False
        database_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not {column.name for column in table.columns}.issubset(database_columns):
            return False
    return True


def upgrade_database(database_url: str) -> None:
    """空库标记当前结构；历史库执行增量迁移，失败即阻断应用启动。"""
    # 异步驱动 → 同步驱动
    sync_url = database_url.replace("+aiosqlite", "").replace("+aiomysql", "+pymysql")
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", sync_url)
    engine = create_engine(sync_url)
    try:
        inspector = inspect(engine)
        has_alembic_version = "alembic_version" in inspector.get_table_names()
        schema_matches_metadata = _schema_matches_metadata(engine)
    finally:
        engine.dispose()

    # 已有 revision 的历史库必须沿迁移链升级，不能因部分字段存在而直接标记 head。
    # 仅 create_all 生成且尚无迁移记录的完整空库可安全标记为当前 head。
    if not has_alembic_version and schema_matches_metadata:
        command.stamp(config, "head")
    else:
        command.upgrade(config, "head")

    validation_engine = create_engine(sync_url)
    try:
        if not _schema_matches_metadata(validation_engine):
            raise RuntimeError("database schema does not match ORM metadata after Alembic upgrade")
    finally:
        validation_engine.dispose()
