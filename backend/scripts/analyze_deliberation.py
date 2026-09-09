#!/usr/bin/env python
"""AI 狼人杀 — 狼队协商对照分析脚本（需求一 FR-5 / 验收5）

数据来源：game_events（night_kill 事件）+ game_players（角色），**不读 agent_logs**。
只报锚点数字，不报显著性结论（对齐成本预算段与验收5）。

输出两类指标：
1. agreement 分布：night_kill 事件的 $.agreement 计数与占比
   - 关闭协商（legacy）：unanimous / human_priority / stable_ai_priority / single_ai_werewolf ...
   - 开启协商（deliberate）：unanimous_first / converged_after_debate / stable_ai_priority / human_priority
2. 刀中神职率：night_kill 的 $.target join game_players.role → 命中 seer/witch/guard 的比例

对照实验流程（验收5）：
    python scripts/batch_games.py --num 50 --mock                       # 关闭组
    WOLF_DELIBERATION_ENABLED=true python scripts/batch_games.py --num 50 --mock   # 开启组
    python scripts/analyze_deliberation.py --label 关闭组
    python scripts/analyze_deliberation.py --label 开启组

用法：
    python scripts/analyze_deliberation.py [--db PATH] [--label NAME] [--game-id ID]

默认数据库路径：./data/werewolf.db（相对于 backend/ 目录）
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 神职集合（与技术方案 G3 一致：seer/witch/guard；默认 roster 下 hunter/guard 计数为 0）
DEITY_ROLES = ("seer", "witch", "guard")


def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def analyze_agreement(conn, game_id: str | None) -> list[dict[str, Any]]:
    """night_kill 事件的 agreement 分布（计数 + 占比）。"""
    sql = (
        "SELECT json_extract(event_data, '$.agreement') AS agreement, COUNT(*) AS cnt "
        "FROM game_events WHERE event_type = 'night_kill'"
    )
    params: list[Any] = []
    if game_id:
        sql += " AND game_id = ?"
        params.append(game_id)
    sql += " GROUP BY agreement ORDER BY cnt DESC"

    rows = conn.execute(sql, params).fetchall()
    total = sum(r["cnt"] for r in rows) or 1
    return [
        {
            "agreement": r["agreement"] if r["agreement"] is not None else "(null)",
            "count": r["cnt"],
            "pct": round(r["cnt"] / total * 100, 1),
        }
        for r in rows
    ]


def analyze_deity_hit(conn, game_id: str | None) -> dict[str, Any]:
    """刀中神职率：night_kill.target join game_players.role。"""
    sql = (
        "SELECT e.game_id AS game_id, "
        "       json_extract(e.event_data, '$.target') AS target, "
        "       p.role AS role "
        "FROM game_events e "
        "LEFT JOIN game_players p "
        "       ON p.game_id = e.game_id "
        "      AND p.seat_number = json_extract(e.event_data, '$.target') "
        "WHERE e.event_type = 'night_kill'"
    )
    params: list[Any] = []
    if game_id:
        sql += " AND e.game_id = ?"
        params.append(game_id)

    rows = conn.execute(sql, params).fetchall()
    total = len(rows)
    by_role: dict[str, int] = {}
    deity_hits = 0
    for r in rows:
        role = r["role"] or "(unknown)"
        by_role[role] = by_role.get(role, 0) + 1
        if role in DEITY_ROLES:
            deity_hits += 1

    return {
        "total_kills": total,
        "deity_hits": deity_hits,
        "deity_hit_rate": round(deity_hits / total * 100, 1) if total else 0.0,
        "by_role": dict(sorted(by_role.items(), key=lambda kv: kv[1], reverse=True)),
    }


def main():
    parser = argparse.ArgumentParser(description="狼队协商对照分析（只报数据，不报结论）")
    parser.add_argument("--db", default="./data/werewolf.db", help="SQLite 数据库路径（默认 ./data/werewolf.db）")
    parser.add_argument("--label", default="", help="分组标签（如 关闭组 / 开启组），仅用于报告标题")
    parser.add_argument("--game-id", help="只分析指定 game_id")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误：数据库文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(str(db_path))

    kill_count = conn.execute(
        "SELECT COUNT(*) FROM game_events WHERE event_type = 'night_kill'"
        + (" AND game_id = ?" if args.game_id else ""),
        [args.game_id] if args.game_id else [],
    ).fetchone()[0]
    if kill_count == 0:
        print("game_events 中无 night_kill 记录，请先跑几局游戏再分析。")
        conn.close()
        sys.exit(0)

    title = f"AI 狼人杀 狼队协商分析报告" + (f"（{args.label}）" if args.label else "")
    print(f"{'='*60}")
    print(title)
    print(f"数据库: {db_path}")
    if args.game_id:
        print(f"过滤 game_id: {args.game_id}")
    print(f"night_kill 事件数: {kill_count}")
    print(f"{'='*60}")

    # 1. agreement 分布
    print(f"\n1. agreement 分布")
    agreement_rows = analyze_agreement(conn, args.game_id)
    for r in agreement_rows:
        print(f"    {r['agreement']:<24} count={r['count']:<5} {r['pct']}%")

    # 2. 刀中神职率
    print(f"\n2. 刀中神职率（seer/witch/guard）")
    deity = analyze_deity_hit(conn, args.game_id)
    print(f"    总刀数: {deity['total_kills']}  命中神职: {deity['deity_hits']}  神职命中率: {deity['deity_hit_rate']}%")
    print(f"    按被刀角色分布:")
    for role, cnt in deity["by_role"].items():
        print(f"      {role:<12} {cnt}")

    print(f"\n{'='*60}")
    print("分析完成（仅锚点数字，不含显著性结论）")
    print(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()
