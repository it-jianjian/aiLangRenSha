"""AI 狼人杀 — 信息隔离过滤器（Context Filter）

职责：根据角色身份过滤游戏上下文，确保每个 Agent 只能看到自己角色权限内的信息
这是 AI 狼人杀的核心安全机制，防止信息泄漏导致博弈失衡

技术方案 §5.2 可见性矩阵：
| 信息类型              | 狼人 | 预言家 | 女巫 | 村民 |
|:--                    |:--: | :--:  |:--: | :--:|
| 自己的角色             | YES | YES   | YES | YES |
| 狼人同伴身份           | YES | NO    | NO  | NO  |
| 预言家查验结果         | NO  | YES   | NO  | NO  |
| 女巫药水状态           | NO  | NO    | YES | NO  |
| 被击杀者(女巫视角)     | NO  | NO    | YES | NO  |
| 自己选的击杀目标       | YES | NO    | NO  | NO  |
| 公开死亡/发言/投票     | YES | YES   | YES | YES |
| 其他玩家角色           | NO  | NO    | NO  | NO  |

调用链：night/day/vote node → filter_context → Agent PromptBuilder
"""

from typing import Any


def filter_context(
    state: dict[str, Any],
    seat_number: int,
    role: str,
    action_type: str,
) -> dict[str, Any]:
    """按角色过滤游戏上下文，返回仅包含该角色可见信息的字典

    参数:
        state: 完整的 GameFlowState（未过滤）
        seat_number: 当前 Agent 的座位号
        role: 当前 Agent 的角色（werewolf/villager/seer/witch）
        action_type: 当前决策类型（kill/verify/save/poison/speech/vote/last_words）

    返回:
        过滤后的上下文字典，仅包含该角色权限内可见的信息

    设计说明:
        - 玩家字典中移除 role 字段（防止信息泄漏）
        - 每个角色只能看到公共信息 + 自己角色的专属信息
        - 函数是纯函数，不修改原始 state
    """

    # ─── 公共信息（所有角色都可见） ─────────────────────────
    # 移除玩家字典中的 role 字段，只保留公开属性
    sanitized_players = []
    for p in state.get("players", []):
        sanitized_players.append({
            "seat_number": p["seat_number"],
            "player_name": p["player_name"],
            "player_type": p["player_type"],
            "is_alive": p["is_alive"],
            "death_round": p.get("death_round"),
            "death_phase": p.get("death_phase"),
            "death_reason": p.get("death_reason"),
        })

    # 存活座位号列表（便捷字段）
    alive_seats = [p["seat_number"] for p in state.get("players", []) if p["is_alive"]]

    result: dict[str, Any] = {
        # 基础信息
        "current_round": state.get("current_round", 0),
        "players": sanitized_players,
        "alive_seats": alive_seats,
        # 当前轮发言/投票（本轮实时数据）
        "speeches": state.get("speeches", []),
        "votes": state.get("votes", {}),
        "eliminated_seat": state.get("eliminated_seat"),
        # 夜晚死亡信息（白天公布后为公共信息）
        "night_deaths": state.get("night_deaths", []),
        # 跨轮历史（让 AI 能看到之前轮次的发言/投票/死亡）
        "game_history": state.get("game_history", []),
        # 当前决策类型（让 Agent 知道要做什么决策）
        "action_type": action_type,
    }

    # ─── 狼人专属信息 ───────────────────────────────────────
    if role == "werewolf":
        # 狼人同伴座位号（排除自己）
        werewolf_seats = state.get("werewolf_seats", [])
        companions = [s for s in werewolf_seats if s != seat_number]
        result["werewolf_companions"] = companions

        # 上一轮击杀目标
        # 已知限制：PRD 要求每个狼人只看到“自己的选择”，但当前实现暴露的是
        # 最终协商目标（所有狼人共享同一个值）。完整实现需从 AgentLog 查询各自选择。
        result["last_kill_target"] = state.get("night_kill_target")

    # ─── 预言家专属信息 ─────────────────────────────────────
    if role == "seer":
        # 预言家的完整历史查验结果（跨轮累积）
        result["seer_results"] = state.get("seer_history", [])

    # ─── 女巫专属信息 ───────────────────────────────────────
    if role == "witch":
        # 药水使用状态
        result["witch_save_used"] = state.get("witch_save_used", False)
        result["witch_poison_used"] = state.get("witch_poison_used", False)

        # 被狼人击杀的玩家（女巫在夜晚行动时需要此信息来决定是否用解药）
        if action_type in ("save", "poison", "skip"):
            result["night_kill_target"] = state.get("night_kill_target")

    return result
