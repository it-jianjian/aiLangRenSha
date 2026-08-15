#!/usr/bin/env python
"""AI 狼人杀 — 批量对局脚本

自动创建并跑完 N 局纯 AI 对局，用于性能基线采集和回归测试。

用法：
    python scripts/batch_games.py [--num N] [--db PATH] [--mock]

参数：
    --num    对局数量（默认 10）
    --db     SQLite 数据库路径（默认 ./data/werewolf.db）
    --mock   使用 Mock LLM 模式（不调用真实 API，仅验证逻辑流程）

跑完后建议运行 analyze_agent_logs.py 查看聚合报告。
"""

import argparse
import asyncio
import secrets
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _create_game_sync(db_path: str, mode: str = "pure_ai") -> str:
    """同步创建一局 pure_ai 游戏（避免 FastAPI 依赖）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    game_id = secrets.token_hex(16)
    now = datetime.now().isoformat()

    # 创建 Game 记录
    cursor.execute(
        "INSERT INTO games (id, mode, status, player_count, roster_type, roster_json, config_json, created_at) "
        "VALUES (?, ?, ?, 6, 'official', "
        "'{\"werewolf\":2,\"villager\":2,\"seer\":1,\"guard\":0,\"hunter\":0,\"witch\":1}', "
        "'{}', ?)",
        (game_id, mode, "waiting", now)
    )

    # 随机分配角色（2狼+2村民+1预言家+1女巫）
    import random
    roles = ["werewolf", "werewolf", "villager", "villager", "seer", "witch"]
    random.shuffle(roles)
    personas = ["冷静分析师", "热情社交家", "逻辑推理者", "直觉玩家", "保守策略家", "冒险挑战者"]
    random.shuffle(personas)

    for seat, (role, persona) in enumerate(zip(roles, personas), start=1):
        cursor.execute(
            "INSERT INTO game_players (id, game_id, seat_number, player_type, role, player_name, ai_persona, is_alive) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (secrets.token_hex(16), game_id, seat, "ai", role, f"AI-{persona}", persona)
        )

    conn.commit()
    conn.close()
    return game_id


def _update_game_status(db_path: str, game_id: str, status: str):
    """更新对局状态"""
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE games SET status = ? WHERE id = ?", (status, game_id))
    conn.commit()
    conn.close()


async def run_batch(num_games: int, db_path: str, mock: bool):
    """批量创建并运行对局"""
    print(f"{'='*60}")
    print(f"批量对局脚本 — 计划跑 {num_games} 局")
    print(f"数据库: {db_path}")
    print(f"模式: {'Mock LLM' if mock else '真实 LLM'}")
    print(f"{'='*60}")

    # 确保数据库目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    results = []
    start_time = time.monotonic()

    for i in range(1, num_games + 1):
        game_id = _create_game_sync(db_path)
        _update_game_status(db_path, game_id, "playing")
        print(f"\n[{i}/{num_games}] 启动对局 {game_id[:8]}...")

        # 使用游戏流程运行
        try:
            from app.graphs.game_flow import run_game
            game_start = time.monotonic()
            await run_game(game_id)
            game_elapsed = time.monotonic() - game_start
            print(f"  [{i}/{num_games}] 完成，耗时 {game_elapsed:.1f}s")
            results.append({"game_id": game_id, "elapsed_s": round(game_elapsed, 1), "status": "finished"})
        except Exception as e:
            elapsed = time.monotonic() - start_time
            print(f"  [{i}/{num_games}] 失败: {e}")
            results.append({"game_id": game_id, "elapsed_s": round(elapsed, 1), "status": "error", "error": str(e)})
            _update_game_status(db_path, game_id, "finished")

    total_elapsed = time.monotonic() - start_time

    # 输出汇总
    print(f"\n{'='*60}")
    print("批量对局汇总")
    print(f"{'='*60}")
    finished = [r for r in results if r["status"] == "finished"]
    errors = [r for r in results if r["status"] == "error"]

    print(f"  总对局: {num_games}")
    print(f"  成功: {len(finished)}, 失败: {len(errors)}")
    if finished:
        times = [r["elapsed_s"] for r in finished]
        print(f"  平均耗时: {sum(times)/len(times):.1f}s")
        print(f"  最快: {min(times):.1f}s, 最慢: {max(times):.1f}s")
    print(f"  总耗时: {total_elapsed:.1f}s")

    # 读取数据库统计
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # 各角色/阵营胜率
        winner_counts = conn.execute(
            "SELECT winner, COUNT(*) as cnt FROM games WHERE status = 'finished' AND winner IS NOT NULL GROUP BY winner"
        ).fetchall()
        total_finished = sum(r["cnt"] for r in winner_counts)
        print("\n  阵营胜率:")
        for row in winner_counts:
            print(f"    {row['winner']}: {row['cnt']}局 ({row['cnt']/total_finished*100:.1f}%)")

        # Fallback 率
        fb = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN is_fallback = 1 THEN 1 ELSE 0 END) as fb "
            "FROM agent_logs"
        ).fetchone()
        if fb["total"] > 0:
            print(f"\n  全局 Fallback 率: {fb['fb']}/{fb['total']} = {fb['fb']/fb['total']*100:.1f}%")

        conn.close()
    except Exception as e:
        print(f"\n  数据库统计读取失败: {e}")

    print(f"\n{'='*60}")
    print("批量对局完成。建议运行 analyze_agent_logs.py 查看详细报告。")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="批量对局脚本")
    parser.add_argument("--num", type=int, default=10, help="对局数量（默认 10）")
    parser.add_argument("--db", default="./data/werewolf.db", help="SQLite 数据库路径")
    parser.add_argument("--mock", action="store_true", help="使用 Mock LLM 模式")
    args = parser.parse_args()

    asyncio.run(run_batch(args.num, args.db, args.mock))


if __name__ == "__main__":
    main()
