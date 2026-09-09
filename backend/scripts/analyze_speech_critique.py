#!/usr/bin/env python
"""AI 狼人杀 — 发言批评-修订对照分析脚本（需求二 FR-4/FR-5 / 验收4）

数据来源：agent_logs（critique_result / revised / latency_ms），只读该表。
只报锚点数字，不报显著性/准确率结论（对齐成本预算段与验收4：没有自动裁判就没有资格说准确率）。

输出四类指标：
1. revised 率：revised=1 占「被审稿发言」（critique_result IS NOT NULL）的比例
2. risk_level 分布：high / medium / none 计数与占比
3. issue 类型分布：identity_leak / self_contradiction / rule_violation / weak_argument
   （哪类穿帮最高频，反哺 draft 阶段 prompt 迭代——本需求的隐藏收益）
4. 时长：被审稿发言 vs 普通发言的 latency_ms 均值 / p50 / p90

说明：Mock 模式下草稿经 run_agent 也会落一行（critique_result 为 NULL），本脚本
一律以 action_type='speech' AND critique_result IS NOT NULL 界定「被审稿发言」，
天然过滤 Mock 双行（见技术方案 D5）。

对照实验流程（验收4）：
    python scripts/batch_games.py --num 50 --mock                              # 关闭组
    SPEECH_CRITIQUE_ENABLED=true python scripts/batch_games.py --num 50 --mock  # 开启组
    python scripts/analyze_speech_critique.py --label 关闭组
    python scripts/analyze_speech_critique.py --label 开启组

用法：
    python scripts/analyze_speech_critique.py [--db PATH] [--label NAME] [--game-id ID]

默认数据库路径：./data/werewolf.db（相对于 backend/ 目录）
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 审稿单可能出现的问题类型（与 prompts.build_critique_prompt 的四类维度一致）
ISSUE_TYPES = ("identity_leak", "self_contradiction", "rule_violation", "weak_argument")


def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _percentile(values: list[int], pct: float) -> float:
    """线性插值分位数（values 非空）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def analyze_revised_rate(conn, game_id: str | None) -> dict[str, Any]:
    """revised 率：revised=1 / 被审稿发言总数。"""
    sql = (
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN revised = 1 THEN 1 ELSE 0 END) AS revised_cnt, "
        "       SUM(CASE WHEN is_fallback = 1 THEN 1 ELSE 0 END) AS fallback_cnt "
        "FROM agent_logs WHERE action_type = 'speech' AND critique_result IS NOT NULL"
    )
    params: list[Any] = []
    if game_id:
        sql += " AND game_id = ?"
        params.append(game_id)
    r = conn.execute(sql, params).fetchone()
    total = r["total"] or 0
    revised_cnt = r["revised_cnt"] or 0
    fallback_cnt = r["fallback_cnt"] or 0
    return {
        "critiqued": total,
        "revised": revised_cnt,
        "revised_rate": round(revised_cnt / total * 100, 1) if total else 0.0,
        "fallback": fallback_cnt,
        "fallback_rate": round(fallback_cnt / total * 100, 1) if total else 0.0,
    }


def analyze_risk_distribution(conn, game_id: str | None) -> list[dict[str, Any]]:
    """risk_level 分布（计数 + 占比）。"""
    sql = (
        "SELECT json_extract(critique_result, '$.risk_level') AS risk, COUNT(*) AS cnt "
        "FROM agent_logs WHERE action_type = 'speech' AND critique_result IS NOT NULL"
    )
    params: list[Any] = []
    if game_id:
        sql += " AND game_id = ?"
        params.append(game_id)
    sql += " GROUP BY risk ORDER BY cnt DESC"
    rows = conn.execute(sql, params).fetchall()
    total = sum(r["cnt"] for r in rows) or 1
    return [
        {
            "risk_level": r["risk"] if r["risk"] is not None else "(null)",
            "count": r["cnt"],
            "pct": round(r["cnt"] / total * 100, 1),
        }
        for r in rows
    ]


def analyze_issue_types(conn, game_id: str | None) -> dict[str, int]:
    """issue 类型分布：解析 critique_result.issue_types 数组后聚合。"""
    sql = (
        "SELECT critique_result FROM agent_logs "
        "WHERE action_type = 'speech' AND critique_result IS NOT NULL"
    )
    params: list[Any] = []
    if game_id:
        sql += " AND game_id = ?"
        params.append(game_id)
    rows = conn.execute(sql, params).fetchall()

    counter: dict[str, int] = {t: 0 for t in ISSUE_TYPES}
    for r in rows:
        try:
            data = json.loads(r["critique_result"])
        except (json.JSONDecodeError, TypeError):
            continue
        for t in data.get("issue_types", []) or []:
            counter[t] = counter.get(t, 0) + 1
    return dict(sorted(counter.items(), key=lambda kv: kv[1], reverse=True))


def analyze_latency(conn, game_id: str | None) -> dict[str, Any]:
    """时长对照：被审稿发言 vs 普通发言（critique_result IS NULL）的 latency_ms。"""
    def _stats(critiqued: bool) -> dict[str, Any]:
        sql = (
            "SELECT latency_ms FROM agent_logs WHERE action_type = 'speech' AND latency_ms IS NOT NULL"
            + (" AND critique_result IS NOT NULL" if critiqued else " AND critique_result IS NULL")
        )
        params: list[Any] = []
        if game_id:
            sql += " AND game_id = ?"
            params.append(game_id)
        vals = [r["latency_ms"] for r in conn.execute(sql, params).fetchall()]
        if not vals:
            return {"count": 0, "mean": 0.0, "p50": 0.0, "p90": 0.0}
        return {
            "count": len(vals),
            "mean": round(sum(vals) / len(vals), 1),
            "p50": _percentile(vals, 0.5),
            "p90": _percentile(vals, 0.9),
        }

    return {"critiqued": _stats(True), "plain": _stats(False)}


def main():
    parser = argparse.ArgumentParser(description="发言批评-修订对照分析（只报数据，不报结论）")
    parser.add_argument("--db", default="./data/werewolf.db", help="SQLite 数据库路径（默认 ./data/werewolf.db）")
    parser.add_argument("--label", default="", help="分组标签（如 关闭组 / 开启组），仅用于报告标题")
    parser.add_argument("--game-id", help="只分析指定 game_id")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误：数据库文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _connect(str(db_path))

    critiqued_count = conn.execute(
        "SELECT COUNT(*) FROM agent_logs WHERE action_type = 'speech' AND critique_result IS NOT NULL"
        + (" AND game_id = ?" if args.game_id else ""),
        [args.game_id] if args.game_id else [],
    ).fetchone()[0]

    title = "AI 狼人杀 发言批评-修订分析报告" + (f"（{args.label}）" if args.label else "")
    print(f"{'='*60}")
    print(title)
    print(f"数据库: {db_path}")
    if args.game_id:
        print(f"过滤 game_id: {args.game_id}")
    print(f"被审稿发言数（critique_result 非空）: {critiqued_count}")
    print(f"{'='*60}")

    if critiqued_count == 0:
        print("\n无被审稿发言记录（speech_critique 可能未开启，或尚未跑批）。")
        print("开启对照：SPEECH_CRITIQUE_ENABLED=true python scripts/batch_games.py --num 50 --mock")
        conn.close()
        sys.exit(0)

    # 1. revised 率
    print("\n1. revised 率 / 降级率")
    rr = analyze_revised_rate(conn, args.game_id)
    print(f"    被审稿: {rr['critiqued']}  修订: {rr['revised']} ({rr['revised_rate']}%)  "
          f"降级(回退草稿): {rr['fallback']} ({rr['fallback_rate']}%)")

    # 2. risk_level 分布
    print("\n2. risk_level 分布")
    for r in analyze_risk_distribution(conn, args.game_id):
        print(f"    {r['risk_level']:<10} count={r['count']:<5} {r['pct']}%")

    # 3. issue 类型分布（哪类穿帮最高频）
    print("\n3. issue 类型分布（穿帮高频榜）")
    for t, cnt in analyze_issue_types(conn, args.game_id).items():
        print(f"    {t:<20} {cnt}")

    # 4. 时长对照
    print("\n4. 发言时长对照（latency_ms）")
    lat = analyze_latency(conn, args.game_id)
    c, p = lat["critiqued"], lat["plain"]
    print(f"    被审稿发言: n={c['count']:<5} mean={c['mean']:<8} p50={c['p50']:<8} p90={c['p90']}")
    print(f"    普通发言  : n={p['count']:<5} mean={p['mean']:<8} p50={p['p50']:<8} p90={p['p90']}")

    print(f"\n{'='*60}")
    print("分析完成（仅锚点数字，不含显著性/准确率结论）")
    print(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()
