"""AI 狼人杀 — LangGraph 游戏流程状态定义

本文件定义了 LangGraph StateGraph 的完整状态结构（GameFlowState）。
每个节点函数接收这个状态，返回需要更新的字段。

调用链：GameFlowGraph(node) → state → node_func(state) → partial_state_update → merge → new_state

核心设计原则：
- 状态是不可变的：节点函数不修改原状态，而是返回一个新的 dict 作为更新
- 所有列表类型字段（players、speeches 等）需要返回完整列表（默认 reducer 是替换）
- P3 阶段会增加 Agent 决策相关字段
"""

from typing import Any, Optional
from typing_extensions import TypedDict


# ================================================================
# 玩家字典结构（players 列表中每个元素的字段约定）
# ================================================================
# 这不是一个 Python 类，而是对 dict 字段的文档约定
# 在代码中通过字典访问：player["seat_number"], player["is_alive"] 等
#
# {
#     "seat_number": int,          # 座位号 1-6（永不改变）
#     "player_name": str,          # 显示名称（永不改变）
#     "player_type": str,          # "human" / "ai"（永不改变）
#     "role": str,                 # "werewolf" / "villager" / "seer" / "witch"（永不改变）
#     "is_alive": bool,            # 是否存活（死亡后变为 False）
#     "ai_persona": str | None,    # AI 人设标识（AI 玩家有值，人类为 None）
#     "death_round": int | None,   # 死亡轮次（存活时为 None）
#     "death_phase": str | None,   # "night" / "day"（存活时为 None）
#     "death_reason": str | None,  # 死因（存活时为 None）
# }


class GameFlowState(TypedDict):
    """LangGraph 游戏主流程状态

    这个 TypedDict 定义了游戏运行时的全部状态。
    LangGraph 的 StateGraph 会在每个节点执行后，将节点返回的 dict 合并到当前状态中。
    对于普通字段（int/str/bool），直接替换；对于列表，也是整体替换。
    """

    # ─── 基础信息（初始化后不改变） ─────────────────────────
    game_id: str                      # 对局 ID（UUID）
    mode: str                         # 游戏模式："pure_ai" / "mixed"
    human_seat: Optional[int]         # 人类玩家座位号（pure_ai 模式为 None）

    # ─── 玩家列表 ──────────────────────────────────────────
    # 6 个玩家字典的列表，每个元素结构见上方 PlayerDict 约定
    # 节点修改玩家状态时，必须返回完整的 players 列表
    players: list[dict[str, Any]]

    # ─── 角色位置缓存（role_assign 节点设置，后续节点快速查找） ─
    werewolf_seats: list[int]         # 狼人座位号列表，如 [2, 5]
    seer_seat: Optional[int]          # 预言家座位号
    witch_seat: Optional[int]         # 女巫座位号
    hunter_seat: Optional[int]
    guard_seat: Optional[int]

    # ─── 回合信息 ───────────────────────────────────────────
    current_round: int                # 当前回合编号（从 1 开始，每轮白天结束后 +1）

    # ─── 夜晚阶段临时数据 ───────────────────────────────────
    # 每个夜晚开始时重置，夜晚结算后清空
    night_kill_target: Optional[int]  # 狼人最终击杀目标座位号
    night_seer_target: Optional[int]  # 预言家查验目标座位号
    night_seer_result: Optional[str]  # 查验结果："werewolf" / "villager"
    night_witch_action: str           # 女巫行动："save" / "poison" / "skip"
    night_witch_target: Optional[int] # 女巫毒药目标（仅 poison 时有值）
    night_guard_target: Optional[int]
    night_deaths: list[int]           # 夜晚最终死亡的座位号列表

    # ─── 女巫药水状态（跨回合持久） ─────────────────────────
    # 解药和毒药各只能用一次（整局游戏内），这里跟踪是否已使用
    witch_save_used: bool             # 解药是否已使用
    witch_poison_used: bool           # 毒药是否已使用
    guard_last_target: Optional[int]
    pending_hunter_shot: Optional[int]

    # ─── 白天阶段临时数据 ───────────────────────────────────
    speeches: list[dict[str, Any]]    # 发言列表 [{"seat": int, "content": str}, ...]
    votes: dict[int, Optional[int]]   # 投票结果 {投票者座位号: 目标座位号(None=弃票)}

    # ─── 投票/淘汰结果 ──────────────────────────────────────
    eliminated_seat: Optional[int]    # 本轮被淘汰座位号
    is_pk: bool                       # 是否正在 PK（平票重投）
    pk_seats: list[int]               # PK 候选人座位号列表
    pre_pk_votes: dict[int, Optional[int]]  # 平票首轮投票（仅 PK 轮有值，保留供 AI 跨轮记忆）
    pk_speeches: list[dict[str, Any]]       # PK 环节发言 [{"seat": int, "content": str}]

    # ─── 胜负判定 ───────────────────────────────────────────
    game_over: bool                   # 游戏是否结束
    winner: Optional[str]             # 获胜方："werewolf" / "villager" / None
    end_reason: Optional[str]         # 结束原因

    # ─── 反循环机制 ─────────────────────────────────────────
    # PRD 规则：连续 3 个平安日（白天无人淘汰）→ 狼人胜
    consecutive_peace_days: int       # 连续平安日计数

    # ─── 胜负检查来源标记 ───────────────────────────────────
    # 用于 route_after_victory 决定下一个节点：
    #   "after_night" → 继续进入白天（day_start）
    #   "after_day"   → 进入下一轮夜晚（night_start）
    post_victory_route: str

    # ─── 跨轮历史信息（AI 推理用，不在夜晚重置） ──────────────
    # 每轮结束时追加本轮摘要，让 AI 在下一轮能看到之前的发言/投票/死亡
    game_history: list[dict[str, Any]]

    # ─── 预言家查验历史（跨轮累积，只有预言家可见） ────────
    seer_history: list[dict[str, Any]]

    # ─── 人类中断点（P3 阶段使用） ──────────────────────────
    # 混合模式下，LangGraph 在需要人类操作的节点前暂停（interrupt_before）
    # 人类通过 REST API 提交操作后，数据存入此字段，图恢复执行时读取
    pending_human_action: Optional[dict[str, Any]]
