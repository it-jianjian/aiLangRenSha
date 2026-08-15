"""AI 狼人杀 — ReAct Agent 只读工具集

三个工具：query_vote_history / query_speech / query_death_history
安全红线：工具返回值必须过信息隔离过滤，狼人用工具查不到女巫用药等私有信息。
"""

import json
import sqlite3
from typing import Any, Optional

from langchain_core.tools import tool


def _query_db(db_path: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """同步查询 SQLite，返回字典列表"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _isolate_vote_result(result: dict, seat_number: int, role: str) -> dict:
    """票型信息隔离：移除不应暴露的私有字段"""
    # 票型本身是公开信息（明票制），无需额外过滤
    return {k: v for k, v in result.items() if k not in ("id",)}


def _isolate_speech(seat: int, role: str, speech: dict) -> dict:
    """发言信息隔离：发言是公开信息，无需额外过滤"""
    return {k: v for k, v in speech.items() if k not in ("id",)}


def _isolate_death(seat: int, role: str, death: dict) -> dict:
    """死亡信息隔离：死因对所有人公开"""
    return {k: v for k, v in death.items() if k not in ("id",)}


@tool
def query_vote_history(
    game_id: str,
    db_path: str,
    seat_number: int,
    role: str,
    round_number: Optional[int] = None,
    player_seat: Optional[int] = None,
) -> str:
    """查询某轮或某人的历史票型。

    Args:
        game_id: 对局 ID
        db_path: SQLite 数据库路径
        seat_number: 当前查询者座位号
        role: 当前查询者角色
        round_number: 可选，指定轮次
        player_seat: 可选，指定玩家座位

    Returns:
        JSON 字符串，包含投票记录
    """
    sql = "SELECT round_number, voter_seat, target_seat, is_pk FROM votes WHERE game_id = ?"
    params: list = [game_id]

    if round_number is not None:
        sql += " AND round_number = ?"
        params.append(round_number)
    if player_seat is not None:
        sql += " AND voter_seat = ?"
        params.append(player_seat)

    sql += " ORDER BY round_number, voter_seat"
    rows = _query_db(db_path, sql, tuple(params))
    rows = [_isolate_vote_result(r, seat_number, role) for r in rows]

    if not rows:
        return "未找到投票记录"
    return json.dumps(rows, ensure_ascii=False)


@tool
def query_speech(
    game_id: str,
    db_path: str,
    seat_number: int,
    role: str,
    round_number: Optional[int] = None,
    player_seat: Optional[int] = None,
) -> str:
    """按座位或轮次查历史发言。

    Args:
        game_id: 对局 ID
        db_path: SQLite 数据库路径
        seat_number: 当前查询者座位号
        role: 当前查询者角色
        round_number: 可选，指定轮次
        player_seat: 可选，指定发言者座位

    Returns:
        JSON 字符串，包含发言记录
    """
    sql = "SELECT round_number, seat_number, content, is_pk, is_last_words FROM chat_messages WHERE game_id = ?"
    params: list = [game_id]

    if round_number is not None:
        sql += " AND round_number = ?"
        params.append(round_number)
    if player_seat is not None:
        sql += " AND seat_number = ?"
        params.append(player_seat)

    sql += " ORDER BY round_number, seat_number"
    rows = _query_db(db_path, sql, tuple(params))
    # 截断过长发言
    for r in rows:
        if len(r.get("content", "")) > 200:
            r["content"] = r["content"][:200] + "..."
        _isolate_speech(seat_number, role, r)

    if not rows:
        return "未找到发言记录"
    return json.dumps(rows, ensure_ascii=False)


@tool
def query_death_history(
    game_id: str,
    db_path: str,
    seat_number: int,
    role: str,
) -> str:
    """查询死亡与死因记录。

    Args:
        game_id: 对局 ID
        db_path: SQLite 数据库路径
        seat_number: 当前查询者座位号
        role: 当前查询者角色

    Returns:
        JSON 字符串，包含死亡记录
    """
    sql = (
        "SELECT seat_number, death_round, death_phase, death_reason "
        "FROM game_players WHERE game_id = ? AND is_alive = 0 "
        "ORDER BY death_round, seat_number"
    )
    rows = _query_db(db_path, sql, (game_id,))
    rows = [_isolate_death(seat_number, role, r) for r in rows]

    if not rows:
        return "暂无死亡记录"
    return json.dumps(rows, ensure_ascii=False)


# 工具列表（供 bind_tools 使用）
REACT_TOOLS = [query_vote_history, query_speech, query_death_history]
