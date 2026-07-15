"""AI 狼人杀 — Agent 桥接节点

职责：在游戏流程节点中调用 Agent 推理子图，替代 P2 阶段的随机决策

调用链：night/day/vote node → call_agent_werewolf/seer/witch/villager() → decision

设计说明：
- 每个角色有专用封装函数，使调用方代码更清晰
- 底层统一调用 run_agent()，专用封装负责角色特定的前后处理
- call_agent() 是通用入口，专用函数在它之上添加语义和校验
"""

import logging
from typing import Any, Optional

from app.graphs.agent_graph import run_agent

logger = logging.getLogger(__name__)


# ================================================================
# 通用入口（所有角色的底层实现）
# ================================================================

def call_agent(
    game_state: dict[str, Any],
    player: dict[str, Any],
    action_type: str,
    llm: Any = None,
) -> Any:
    """调用 Agent 推理子图，返回决策值（通用入口）

    参数:
        game_state: 完整的 GameFlowState
        player: 玩家字典（含 seat_number, role, player_type 等）
        action_type: 决策类型（kill/verify/save/poison/speech/vote/last_words）
        llm: 可选的 LLM 实例（默认从配置创建）

    返回:
        决策值（类型取决于 action_type）：
        - kill/verify: int（座位号）
        - save: bool
        - poison/vote: int 或 None
        - speech/last_words: str
    """
    agent_state = {
        "seat_number": player["seat_number"],
        "role": player["role"],
        "player_name": player.get("player_name", f"{player['seat_number']}号"),
        "action_type": action_type,
        "game_context": game_state,
    }
    if llm is not None:
        agent_state["llm"] = llm

    result = run_agent(agent_state)
    return result["decision"]


# ================================================================
# 狼人专用封装
# ================================================================

def call_agent_werewolf_kill(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> int:
    """狼人选择击杀目标

    职责：调用 Agent 为狼人选择今晚的击杀目标
    Agent 内部会通过 ContextFilter 获取狼人同伴信息，
    并通过 validate_decision 校验不会击杀同伴

    参数:
        game_state: 完整的 GameFlowState
        player: 狼人玩家字典
        llm: 可选的 LLM 实例

    返回:
        击杀目标座位号（int）
    """
    target = call_agent(game_state, player, "kill", llm)
    if not isinstance(target, int):
        # Agent 决策不合法时已由 run_agent 内部降级处理
        # 这里作为二次保障
        alive_non_wolf = [
            p["seat_number"] for p in game_state.get("players", [])
            if p["is_alive"]
            and p["role"] != "werewolf"
            and p["seat_number"] != player["seat_number"]
        ]
        import random
        target = random.choice(alive_non_wolf) if alive_non_wolf else 1
        logger.warning(f"[Agent] 狼人{player['seat_number']}号 决策异常，降级随机: {target}")
    return target


# ================================================================
# 预言家专用封装
# ================================================================

def call_agent_seer_verify(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> int:
    """预言家选择查验目标

    职责：调用 Agent 为预言家选择今晚的查验目标
    Agent 会根据已有信息推理出最可疑的玩家

    参数:
        game_state: 完整的 GameFlowState
        player: 预言家玩家字典
        llm: 可选的 LLM 实例

    返回:
        查验目标座位号（int）
    """
    target = call_agent(game_state, player, "verify", llm)
    if not isinstance(target, int):
        alive_others = [
            p["seat_number"] for p in game_state.get("players", [])
            if p["is_alive"] and p["seat_number"] != player["seat_number"]
        ]
        import random
        target = random.choice(alive_others) if alive_others else 1
        logger.warning(f"[Agent] 预言家{player['seat_number']}号 决策异常，降级随机: {target}")
    return target


# ================================================================
# 女巫专用封装
# ================================================================

def call_agent_witch(
    game_state: dict[str, Any],
    player: dict[str, Any],
    save_available: bool,
    poison_available: bool,
    alive_other_seats: list[int],
    llm: Any = None,
) -> tuple[str, Optional[int]]:
    """女巫决策：save/poison/skip 三选一

    职责：调用 Agent 为女巫决策今晚的用药策略

    参数:
        game_state: 完整的 GameFlowState
        player: 女巫玩家字典
        save_available: 解药是否可用
        poison_available: 毒药是否可用
        alive_other_seats: 存活的非自己座位号列表
        llm: 可选的 LLM 实例

    返回:
        (action, target) 元组
        - ("save", None) — 使用解药
        - ("poison", seat) — 使用毒药
        - ("skip", None) — 不用药
    """
    # 优先判断解药（因为女巫每晚只能用一种药）
    if save_available:
        save_decision = call_agent(game_state, player, "save", llm)
        if save_decision is True:
            return ("save", None)

    # 再判断毒药
    if poison_available:
        poison_decision = call_agent(game_state, player, "poison", llm)
        if poison_decision is not None and poison_decision in alive_other_seats:
            return ("poison", poison_decision)

    return ("skip", None)


# ================================================================
# 村民（及所有角色通用）专用封装
# ================================================================

def call_agent_villager_speech(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> str:
    """村民（或任何角色）公开发言

    职责：调用 Agent 根据当前局势生成有策略性的发言
    村民通过发言分享推理，狼人通过发言伪装身份

    参数:
        game_state: 完整的 GameFlowState
        player: 发言玩家字典
        llm: 可选的 LLM 实例

    返回:
        发言文本（str，1-500字）
    """
    content = call_agent(game_state, player, "speech", llm)
    if not isinstance(content, str) or len(content.strip()) == 0:
        content = f"{player['seat_number']}号选择沉默。"
        logger.warning(f"[Agent] {player['seat_number']}号 发言异常，降级为沉默")
    return content


def call_agent_villager_vote(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> Optional[int]:
    """村民（或任何角色）投票

    职责：调用 Agent 根据推理结果投票
    好人会投可疑玩家，狼人可能故意投错以伪装身份

    参数:
        game_state: 完整的 GameFlowState
        player: 投票玩家字典
        llm: 可选的 LLM 实例

    返回:
        投票目标座位号（int）或 None（弃票）
    """
    target = call_agent(game_state, player, "vote", llm)
    if target is not None and not isinstance(target, int):
        target = None  # 非法值 → 弃票
        logger.warning(f"[Agent] {player['seat_number']}号 投票异常，降级为弃票")
    return target


def call_agent_last_words(
    game_state: dict[str, Any],
    player: dict[str, Any],
    llm: Any = None,
) -> str:
    """任何角色被淘汰时的遗言

    职责：调用 Agent 生成遗言（可包含关键信息传递）

    参数:
        game_state: 完整的 GameFlowState
        player: 被淘汰玩家字典
        llm: 可选的 LLM 实例

    返回:
        遗言文本（str）
    """
    content = call_agent(game_state, player, "last_words", llm)
    if not isinstance(content, str) or len(content.strip()) == 0:
        content = f"{player['seat_number']}号没有留下遗言。"
        logger.warning(f"[Agent] {player['seat_number']}号 遗言异常，降级默认文本")
    return content
