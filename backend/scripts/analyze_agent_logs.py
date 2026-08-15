#!/usr/bin/env python
"""AI 狼人杀 — AgentLog 离线分析脚本

从 agent_logs 表读取数据，输出三类聚合报告：
1. 按 action_type 聚合：调用次数、latency P50/P95/max、fallback 率、平均 token 数
2. 按 round_number 聚合：latency 均值与 prompt_text 平均长度趋势（验证 Prompt 膨胀假设）
3. 按 game_id 聚合：每局总耗时、总 fallback 数

用法：
    python scripts/analyze_agent_logs.py [--db PATH] [--game-id ID]

默认数据库路径：./data/werewolf.db（相对于 backend/ 目录）
"""

import argparse
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _pct(values: list[float], pct: int) -> float:
    """计算百分位数"""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * pct / 100)
    idx = min(idx, len(sorted_v) - 1)
    return sorted_v[idx]


def analyze_by_action_type(conn) -> list[dict[str, Any]]:
    """按 action_type 聚合统计"""
    cursor = conn.execute(
        "SELECT action_type, COUNT(*) as cnt, latency_ms, is_fallback, "
        "prompt_tokens, completion_tokens, prompt_text "
        "FROM agent_logs ORDER BY action_type"
    )

    rows = cursor.fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["action_type"]
        if key not in groups:
            groups[key] = {"latencies": [], "fallbacks": 0, "prompt_tokens": [], "completion_tokens": [], "prompt_lengths": []}
        g = groups[key]
        if row["latency_ms"] is not None:
            g["latencies"].append(row["latency_ms"])
        g["fallbacks"] += (1 if row["is_fallback"] else 0)
        if row["prompt_tokens"] is not None:
            g["prompt_tokens"].append(row["prompt_tokens"])
        if row["completion_tokens"] is not None:
            g["completion_tokens"].append(row["completion_tokens"])
        if row["prompt_text"] is not None:
            g["prompt_lengths"].append(len(row["prompt_text"]))

    results = []
    for action, g in sorted(groups.items()):
        lats = g["latencies"]
        results.append({
            "action_type": action,
            "count": g["count"] if (g.get("count")) else len(lats) or len(g["prompt_lengths"]),
            "latency_p50_ms": round(_pct(lats, 50), 1) if lats else None,
            "latency_p95_ms": round(_pct(lats, 95), 1) if lats else None,
            "latency_max_ms": max(lats) if lats else None,
            "fallback_rate": f"{g['fallbacks'] / max(len(lats), 1) * 100:.1f}%",
            "avg_prompt_tokens": round(statistics.mean(g["prompt_tokens"]), 1) if g["prompt_tokens"] else None,
            "avg_completion_tokens": round(statistics.mean(g["completion_tokens"]), 1) if g["completion_tokens"] else None,
            "avg_prompt_len": round(statistics.mean(g["prompt_lengths"]), 1) if g["prompt_lengths"] else None,
        })

    # Recount properly
    for r in results:
        action = r["action_type"]
        r["count"] = groups[action]["count"] = len(groups[action]["latencies"]) or len(groups[action]["prompt_lengths"])

    return results


def analyze_by_round(conn) -> list[dict[str, Any]]:
    """按 round_number 聚合：验证 Prompt 膨胀假设"""
    cursor = conn.execute(
        "SELECT round_number, latency_ms, prompt_text "
        "FROM agent_logs ORDER BY round_number"
    )
    rows = cursor.fetchall()
    groups: dict[int, dict[str, list]] = {}
    for row in rows:
        rnd = row["round_number"]
        if rnd not in groups:
            groups[rnd] = {"latencies": [], "prompt_lengths": []}
        if row["latency_ms"] is not None:
            groups[rnd]["latencies"].append(row["latency_ms"])
        if row["prompt_text"] is not None:
            groups[rnd]["prompt_lengths"].append(len(row["prompt_text"]))

    results = []
    for rnd in sorted(groups.keys()):
        g = groups[rnd]
        results.append({
            "round": rnd,
            "avg_latency_ms": round(statistics.mean(g["latencies"]), 1) if g["latencies"] else None,
            "max_latency_ms": max(g["latencies"]) if g["latencies"] else None,
            "avg_prompt_len": round(statistics.mean(g["prompt_lengths"]), 1) if g["prompt_lengths"] else None,
            "max_prompt_len": max(g["prompt_lengths"]) if g["prompt_lengths"] else None,
            "calls": len(g["latencies"]),
        })
    return results


def analyze_by_game(conn) -> list[dict[str, Any]]:
    """按 game_id 聚合：每局总耗时、总 fallback 数"""
    cursor = conn.execute(
        "SELECT game_id, latency_ms, is_fallback, prompt_tokens, completion_tokens "
        "FROM agent_logs ORDER BY game_id"
    )
    rows = cursor.fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        gid = row["game_id"]
        if gid not in groups:
            groups[gid] = {"latencies": [], "fallbacks": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0}
        if row["latency_ms"] is not None:
            groups[gid]["latencies"].append(row["latency_ms"])
        groups[gid]["fallbacks"] += (1 if row["is_fallback"] else 0)
        groups[gid]["total_prompt_tokens"] += (row["prompt_tokens"] or 0)
        groups[gid]["total_completion_tokens"] += (row["completion_tokens"] or 0)

    results = []
    for gid in sorted(groups.keys()):
        g = groups[gid]
        total_lat = sum(g["latencies"])
        results.append({
            "game_id": gid[:8] + "..." if len(gid) > 8 else gid,
            "total_latency_s": round(total_lat / 1000, 1),
            "avg_latency_ms": round(statistics.mean(g["latencies"]), 1) if g["latencies"] else None,
            "fallback_count": g["fallbacks"],
            "total_prompt_tokens": g["total_prompt_tokens"] or None,
            "total_completion_tokens": g["total_completion_tokens"] or None,
            "calls": len(g["latencies"]),
        })
    return results


def cleanup_old_logs(db_path: str, days: int):
    """清理 N 天前的 agent_logs 记录"""
    conn = sqlite3.connect(db_path)
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cursor = conn.execute("DELETE FROM agent_logs WHERE created_at < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"已清理 {deleted} 条 {days} 天前的 agent_logs 记录")


def main():
    parser = argparse.ArgumentParser(description="AgentLog 离线分析")
    parser.add_argument("--db", default="./data/werewolf.db", help="SQLite 数据库路径（默认 ./data/werewolf.db）")
    parser.add_argument("--game-id", help="只分析指定 game_id")
    parser.add_argument("--cleanup-days", type=int, help="清理 N 天前的 agent_logs 记录（D2 膨胀治理）")
    args = parser.parse_args()

    # D2: 清理模式
    if args.cleanup_days:
        cleanup_old_logs(args.db, args.cleanup_days)
        return

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误：数据库文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(str(db_path))
    where_clause = f" WHERE game_id = '{args.game_id}'" if args.game_id else ""

    # 检查是否有数据
    count = conn.execute(f"SELECT COUNT(*) FROM agent_logs{where_clause}").fetchone()[0]
    if count == 0:
        print("agent_logs 表为空，请先跑几局游戏再分析。")
        sys.exit(0)

    print(f"{'='*60}")
    print("AI 狼人杀 AgentLog 分析报告")
    print(f"数据库: {db_path}")
    if args.game_id:
        print(f"过滤 game_id: {args.game_id}")
    print(f"总记录数: {count}")
    print(f"{'='*60}")

    # 1. 按 action_type 聚合
    print(f"\n{'='*60}")
    print("1. 按 action_type 聚合")
    print(f"{'='*60}")
    cursor = conn.execute(f"""
        SELECT action_type,
               COUNT(*) as cnt,
               is_fallback
        FROM agent_logs{where_clause}
        GROUP BY action_type
        ORDER BY action_type
    """)
    action_rows = cursor.fetchall()

    # 获取详细延迟数据
    for row in action_rows:
        action = row["action_type"]
        lat_cursor = conn.execute(
            f"SELECT latency_ms, prompt_tokens, completion_tokens, prompt_text FROM agent_logs WHERE action_type = ?{'' if not args.game_id else ' AND game_id = ?'} ORDER BY latency_ms",
            [action] if not args.game_id else [action, args.game_id]
        )
        lat_rows = lat_cursor.fetchall()
        lats = [r["latency_ms"] for r in lat_rows if r["latency_ms"] is not None]
        fb = conn.execute(
            f"SELECT COUNT(*) FROM agent_logs WHERE action_type = ? AND is_fallback = 1{'' if not args.game_id else ' AND game_id = ?'}",
            [action] if not args.game_id else [action, args.game_id]
        ).fetchone()[0]
        pt_tokens = [r["prompt_tokens"] for r in lat_rows if r["prompt_tokens"] is not None]
        ct_tokens = [r["completion_tokens"] for r in lat_rows if r["completion_tokens"] is not None]
        p_lens = [len(r["prompt_text"]) for r in lat_rows if r["prompt_text"] is not None]

        print(f"\n  {action} (调用 {row['cnt']} 次, fallback {fb}次, 率={fb/max(row['cnt'],1)*100:.1f}%)")
        if lats:
            print(f"    延迟: P50={_pct(lats, 50):.0f}ms  P95={_pct(lats, 95):.0f}ms  Max={max(lats)}ms  Avg={statistics.mean(lats):.0f}ms")
        if pt_tokens:
            print(f"    Prompt tokens:  avg={statistics.mean(pt_tokens):.0f}")
        if ct_tokens:
            print(f"    Completion tokens: avg={statistics.mean(ct_tokens):.0f}")
        if p_lens:
            print(f"    Prompt 长度:  avg={statistics.mean(p_lens):.0f}  max={max(p_lens)}")

    # 2. 按 round_number 聚合
    print(f"\n{'='*60}")
    print("2. 按 round_number 聚合（验证 Prompt 膨胀）")
    print(f"{'='*60}")
    round_cursor = conn.execute(f"""
        SELECT round_number,
               COUNT(*) as cnt
        FROM agent_logs{where_clause}
        GROUP BY round_number
        ORDER BY round_number
    """)
    round_rows = round_cursor.fetchall()
    for row in round_rows:
        rnd = row["round_number"]
        lat_cursor = conn.execute(
            f"SELECT latency_ms, prompt_text FROM agent_logs WHERE round_number = ?{'' if not args.game_id else ' AND game_id = ?'}",
            [rnd] if not args.game_id else [rnd, args.game_id]
        )
        lat_rows = lat_cursor.fetchall()
        lats = [r["latency_ms"] for r in lat_rows if r["latency_ms"] is not None]
        p_lens = [len(r["prompt_text"]) for r in lat_rows if r["prompt_text"] is not None]
        print(f"  Round {rnd}: calls={row['cnt']}, "
              f"latency avg={statistics.mean(lats):.0f}ms" if lats else f"  Round {rnd}: calls={row['cnt']}, no latency data",
              f"prompt avg={statistics.mean(p_lens):.0f} chars" if p_lens else "")

    # 3. 按 game_id 聚合
    print(f"\n{'='*60}")
    print("3. 按 game_id 聚合")
    print(f"{'='*60}")
    game_cursor = conn.execute(f"""
        SELECT game_id, COUNT(*) as cnt
        FROM agent_logs{where_clause}
        GROUP BY game_id
        ORDER BY MIN(created_at)
    """)
    game_rows = game_cursor.fetchall()
    for row in game_rows:
        gid = row["game_id"]
        lat_cursor = conn.execute(
            "SELECT latency_ms, is_fallback, prompt_tokens, completion_tokens FROM agent_logs WHERE game_id = ?",
            [gid]
        )
        lat_rows = lat_cursor.fetchall()
        lats = [r["latency_ms"] for r in lat_rows if r["latency_ms"] is not None]
        fbs = sum(1 for r in lat_rows if r["is_fallback"])
        total_pt = sum(r["prompt_tokens"] or 0 for r in lat_rows)
        total_ct = sum(r["completion_tokens"] or 0 for r in lat_rows)
        print(f"  Game {gid[:8]}... : calls={row['cnt']}, "
              f"total_latency={sum(lats)/1000:.1f}s, "
              f"avg_latency={statistics.mean(lats):.0f}ms, "
              f"fallbacks={fbs}, "
              f"prompt_tokens={total_pt}, completion_tokens={total_ct}" if lats else
              f"  Game {gid[:8]}... : calls={row['cnt']}, no latency data")

    print(f"\n{'='*60}")
    print("分析完成")
    print(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()
