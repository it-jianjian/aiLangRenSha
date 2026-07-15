"""AI 狼人杀 — 事件总线（事件记录 + WebSocket 广播）

职责：
1. 记录游戏事件到 GameEvent 表（用于回放）
2. 广播事件到 WebSocket 连接（用于实时推送，P3 阶段实现 WS 部分）
3. 提供通用的 AI 随机决策函数（P2 临时方案，P3 替换为 LangChain Agent）

调用链：node_func → record_event → DB + WS广播
         node_func → ai_xxx_decision → random choice

设计说明：
- 每个函数独立创建 DB session，避免跨节点共享 session 的生命周期问题
- WS 广播在 P2 阶段为桩实现（仅 logging），P3 阶段接入 WebSocket Manager
- AI 随机决策函数在 P3 阶段会被 Agent 子图替换，当前提供基础可玩性
"""

import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Optional

from app.db.session import async_session_factory
from app.models.game import GameEvent, ChatMessage, GamePlayer, PlayerRole, PlayerType

logger = logging.getLogger(__name__)


# ================================================================
# 事件记录（写入 DB + 日志）
# ================================================================

async def record_event(
    game_id: str,
    round_number: int,
    phase: str,
    event_type: str,
    seat_number: Optional[int] = None,
    event_data: Optional[dict] = None,
):
    """记录游戏事件到数据库 + 日志

    参数:
        game_id: 对局 ID
        round_number: 当前回合编号
        phase: "night" / "day" / "system"
        event_type: 事件类型（对应 EventType 枚举）
        seat_number: 执行者座位号（可选）
        event_data: 事件详细数据 JSON（可选）

    工作流程:
        1. 创建 GameEvent 记录写入数据库
        2. 输出日志（调试用）
        3. 广播到 WebSocket（前端实时推送）
    """
    # ─── 写入数据库 ───
    async with async_session_factory() as session:
        event = GameEvent(
            game_id=game_id,
            round_number=round_number,
            phase=phase,
            event_type=event_type,
            seat_number=seat_number,
            event_data=json.dumps(event_data, ensure_ascii=False) if event_data else None,
        )
        session.add(event)
        await session.commit()

    # ─── 日志输出（调试 + 审计） ───
    logger.info(
        f"[Event] R{round_number} {phase}/{event_type} "
        f"seat={seat_number} data={event_data}"
    )

    # ─── WebSocket 广播 ───
    # 夜晚事件的敏感信息（击杀目标、查验结果、女巫行动）不广播给所有玩家
    # 私有信息已通过 private_* 消息单独发送给对应角色
    try:
        from app.api.ws_handler import ws_manager

        # 夜晚事件过滤：只广播主持人风格的通用消息，不泄露细节
        _night_event_public = {
            "night_kill": {"type": "night_kill", "data": {"round": round_number}},
            "night_verify": {"type": "night_verify", "data": {"round": round_number}},
            "night_save": {"type": "night_save", "data": {"round": round_number}},
            "night_poison": {"type": "night_poison", "data": {"round": round_number}},
            "night_witch_skip": {"type": "night_save", "data": {"round": round_number}},
            "night_settle": None,  # 不广播夜晚结算细节，死亡信息由白天 death_announce 公布
        }

        if phase == "night" and event_type in _night_event_public:
            public_msg = _night_event_public[event_type]
            if public_msg is not None:
                await ws_manager.broadcast(game_id, public_msg)
            # night_settle 直接不广播
        else:
            # 非夜晚事件正常广播
            ws_message = {
                "type": event_type,
                "data": {"round": round_number, "seat": seat_number, **(event_data or {})},
            }
            await ws_manager.broadcast(game_id, ws_message)
    except Exception as e:
        logger.debug(f"[EventBus] WebSocket 广播失败: {e}")


async def save_speech(game_id: str, round_number: int, seat_number: int, content: str, is_pk: bool = False):
    """保存发言到 ChatMessage 表

    参数:
        game_id: 对局 ID
        round_number: 当前回合
        seat_number: 发言者座位号
        content: 发言内容文本
        is_pk: 是否为 PK 环节发言
    """
    async with async_session_factory() as session:
        msg = ChatMessage(
            game_id=game_id,
            round_number=round_number,
            seat_number=seat_number,
            content=content,
            is_pk=is_pk,
        )
        session.add(msg)
        await session.commit()


# ================================================================
# 辅助函数
# ================================================================

def get_alive_players(players: list[dict]) -> list[dict]:
    """获取所有存活玩家"""
    return [p for p in players if p["is_alive"]]


def get_alive_by_role(players: list[dict], role: str) -> list[dict]:
    """获取指定角色的存活玩家"""
    return [p for p in players if p["is_alive"] and p["role"] == role]


def kill_player(players: list[dict], seat: int, round_number: int, phase: str, reason: str) -> list[dict]:
    """标记玩家死亡，返回更新后的完整玩家列表

    参数:
        players: 当前玩家列表
        seat: 死亡玩家座位号
        round_number: 死亡轮次
        phase: "night" / "day"
        reason: "killed_by_werewolf" / "poisoned" / "voted_out"

    返回:
        更新后的完整玩家列表（LangGraph 状态替换机制要求返回完整列表）
    """
    updated = []
    for p in players:
        p_copy = dict(p)  # 浅拷贝，避免修改原状态
        if p_copy["seat_number"] == seat:
            p_copy["is_alive"] = False
            p_copy["death_round"] = round_number
            p_copy["death_phase"] = phase
            p_copy["death_reason"] = reason
        updated.append(p_copy)
    return updated


# ================================================================
# AI 随机决策（P2 临时方案，P3 替换为 LangChain Agent）
# ================================================================
# P2 阶段所有 AI 决策都是随机的，确保游戏流程可以端到端跑通
# P3 阶段会把这些函数替换为 Agent 子图调用

def ai_werewolf_kill_choice(alive_non_werewolf_seats: list[int]) -> int:
    """AI 狼人随机选择击杀目标

    P2: 从存活的非狼人玩家中随机选一个
    P3: 由 Agent 子图根据策略推理选择（优先击杀预言家/女巫等）
    """
    return random.choice(alive_non_werewolf_seats)


def ai_seer_verify_choice(alive_other_seats: list[int]) -> int:
    """AI 预言家随机选择查验目标

    P2: 从存活的其他玩家中随机选一个
    P3: 优先查验可疑玩家（根据发言和行为推理）
    """
    return random.choice(alive_other_seats)


def ai_witch_action_choice(
    save_available: bool,
    poison_available: bool,
    is_first_night: bool,
    alive_other_seats: list[int],
) -> tuple[str, Optional[int]]:
    """AI 女巫决策（P2 简单策略）

    P2 策略（简单规则，非 AI 推理）:
    - 解药可用时: 60% 概率使用（第一夜 80%，因为第一夜救活很关键）
    - 毒药可用时: 20% 概率使用（比较保守，避免误杀好人）
    - 每晚只能用一种药（互斥）: 先判断解药，不用解药才考虑毒药

    返回:
        (action, target) 元组
        - ("save", None)  — 使用解药（目标固定是被狼人杀的人，由调用方处理）
        - ("poison", seat) — 使用毒药，seat 为随机选择的存活非自己玩家
        - ("skip", None)  — 不用药
    """
    # 先判断解药
    if save_available:
        save_prob = 0.8 if is_first_night else 0.6
        if random.random() < save_prob:
            return ("save", None)

    # 再判断毒药（只有不用解药时才考虑）
    if poison_available and alive_other_seats:
        if random.random() < 0.2:
            return ("poison", random.choice(alive_other_seats))

    return ("skip", None)


def ai_speech_text(seat: int, role: str, round_number: int) -> str:
    """AI 生成发言文本（P2 占位文本）

    P2: 返回固定模板文本，包含座位号和回合信息
    P3: 由 LangChain Agent 根据上下文生成有策略性的发言
    """
    templates = [
        f"我是{seat}号玩家。第{round_number}轮，我认为我们需要仔细分析当前的局势。",
        f"第{round_number}轮了，我是{seat}号。目前信息还不够，我建议大家多分享自己的推理。",
        f"{seat}号发言。根据前面的信息，我有一些想法，但还在验证中。",
        f"大家好，我是{seat}号。这轮我觉得我们应该关注一下最近的行为模式。",
    ]
    return random.choice(templates)


def ai_vote_choice(alive_other_seats: list[int]) -> Optional[int]:
    """AI 投票选择

    P2: 80% 概率随机投一个人，20% 概率弃票
    P3: 由 Agent 根据推理结果投票（可能故意投错以伪装身份）

    返回:
        目标座位号（int）或 None（弃票）
    """
    if random.random() < 0.8 and alive_other_seats:
        return random.choice(alive_other_seats)
    return None  # 弃票


def ai_last_words_text(seat: int) -> str:
    """AI 遗言文本（P2 占位）"""
    templates = [
        f"我是{seat}号，虽然我离开了，但希望大家能找出真正的狼人。",
        f"{seat}号遗言：注意观察每个人的投票模式，那是最重要的线索。",
        f"走了，{seat}号留个话——相信自己的判断，不要被误导。",
    ]
    return random.choice(templates)
