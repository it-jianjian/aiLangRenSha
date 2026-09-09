"""数据库后端切换（SQLite ↔ MySQL）专项测试。

覆盖两处按 database_url 分支的关键适配：
1. react_tools._query_db：SQLite 走 db_path 直连；MySQL 复用带池同步引擎 + ?→%s 占位符转换。
2. db.checkpoint.get_checkpointer：SQLite → AsyncSqliteSaver；MySQL → AIOMySQLSaver（含 setup）。

MySQL 分支用 fake 引擎/saver 验证「选对后端 + 转换正确」，无需真实 MySQL 服务。
"""

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import app.db.checkpoint as checkpoint
import app.graphs.agent_graph as agent_graph
from app.agent.react_tools import _query_db


# ─── react_tools._query_db 分支 ─────────────────────────────


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeConn:
    def __init__(self, captured):
        self._captured = captured

    def exec_driver_sql(self, sql, params):
        self._captured["sql"] = sql
        self._captured["params"] = params
        return [_FakeRow({"round_number": 1, "voter_seat": 3, "target_seat": 5, "is_pk": 0})]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, captured, paramstyle="pyformat"):
        self.dialect = SimpleNamespace(paramstyle=paramstyle)
        self._captured = captured

    def connect(self):
        return _FakeConn(self._captured)


def test_query_db_mysql_branch_converts_placeholders(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("app.config.get_settings",
                        lambda: SimpleNamespace(database_url="mysql+pymysql://u:p@localhost:3306/ai_langrensha"))
    monkeypatch.setattr(agent_graph, "_get_sync_engine", lambda: _FakeEngine(captured))

    rows = _query_db("/ignored.db",
                     "SELECT round_number, voter_seat, target_seat, is_pk FROM votes WHERE game_id = ? AND round_number = ?",
                     ("g1", 2))

    # ? 全部转成 %s，且不再残留 ?
    assert "%s" in captured["sql"] and "?" not in captured["sql"]
    assert captured["params"] == ("g1", 2)
    assert rows == [{"round_number": 1, "voter_seat": 3, "target_seat": 5, "is_pk": 0}]


def test_query_db_sqlite_branch_uses_db_path_not_engine(monkeypatch, tmp_path):
    """SQLite 分支：直连 db_path（现有契约），绝不触碰同步引擎。"""
    monkeypatch.setattr("app.config.get_settings",
                        lambda: SimpleNamespace(database_url="sqlite+aiosqlite:///./data/x.db"))

    db_file = str(tmp_path / "tools.db")
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE votes (game_id TEXT, round_number INT, voter_seat INT, target_seat INT, is_pk INT)")
    conn.execute("INSERT INTO votes VALUES ('g1', 1, 3, 5, 0)")
    conn.commit()
    conn.close()

    def _boom():  # pragma: no cover - SQLite 分支不得调用
        raise AssertionError("SQLite 分支不应调用 _get_sync_engine")

    monkeypatch.setattr(agent_graph, "_get_sync_engine", _boom)

    rows = _query_db(db_file, "SELECT round_number, voter_seat FROM votes WHERE game_id = ?", ("g1",))
    assert rows == [{"round_number": 1, "voter_seat": 3}]


# ─── get_checkpointer 分支 ──────────────────────────────────


def test_get_checkpointer_sqlite_branch(monkeypatch, tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    monkeypatch.setattr(checkpoint, "get_settings",
                        lambda: SimpleNamespace(database_url="sqlite+aiosqlite:///./data/x.db", data_dir=tmp_path))

    async def _run():
        async with checkpoint.get_checkpointer() as saver:
            return saver

    saver = asyncio.run(_run())
    assert isinstance(saver, AsyncSqliteSaver)


def test_get_checkpointer_mysql_branch_runs_setup(monkeypatch):
    captured: dict = {}

    class _FakeMySQLSaver:
        @classmethod
        @asynccontextmanager
        async def from_conn_string(cls, url, **kw):
            captured["url"] = url
            yield cls()

        async def setup(self):
            captured["setup"] = True

    monkeypatch.setattr(checkpoint, "_mysql_setup_done", False)
    monkeypatch.setattr("langgraph.checkpoint.mysql.aio.AIOMySQLSaver", _FakeMySQLSaver)
    monkeypatch.setattr(checkpoint, "get_settings", lambda: SimpleNamespace(
        database_url="mysql+aiomysql://root:pw@localhost:3306/ai_langrensha?charset=utf8mb4",
        data_dir=Path("./data"),
    ))

    async def _run():
        async with checkpoint.get_checkpointer() as saver:
            return type(saver).__name__

    name = asyncio.run(_run())
    assert name == "_FakeMySQLSaver"
    assert captured.get("setup") is True, "MySQL 检查点首次使用必须 await setup() 建表"
    assert captured["url"].startswith("mysql+aiomysql://")
