"""AI 狼人杀 — Prompt 模板定义

职责：为 AI Agent 构建结构化的 Prompt 消息列表
技术方案 §5.3 对应实现

Prompt 四部分组装：
1. System Prompt — 通用规则 + 座位号 + 角色
2. Role Strategy — 角色专属策略指引
3. Game Context  — 当前局势（经 context_filter 过滤后）
4. Decision Instruction — 当前决策类型 + 输出格式要求

调用链：AgentGraph(PromptBuildNode) → build_agent_prompt → list[BaseMessage]
"""

import json
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage


# ================================================================
# 角色策略文本
# ================================================================

def get_role_strategy(role: str) -> str:
    """获取角色专属的策略指引文本

    参数:
        role: 角色标识（werewolf/villager/seer/witch）

    返回:
        角色策略描述文本
    """
    strategies = {
        "werewolf": (
            "你是狼人。你的核心目标是伪装成好人，隐藏真实身份，同时在夜晚选择最有威胁的好人进行击杀。\n"
            "策略要点：\n"
            "- 白天发言时伪装成好人，避免暴露狼人身份\n"
            "- 观察其他玩家的发言，找出预言家和女巫等关键角色\n"
            "- 优先击杀对狼人威胁大的角色（如预言家、女巫）\n"
            "- 和你的狼人同伴配合，但不要在游戏中暴露同伴关系"
        ),
        "villager": (
            "你是村民。你没有特殊技能，但你的推理和投票至关重要。\n"
            "策略要点：\n"
            "- 仔细分析每个玩家的发言和行为模式\n"
            "- 关注投票模式——狼人的投票往往会暴露身份\n"
            "- 在发言中分享你的推理过程，帮助好人阵营达成共识\n"
            "- 不要轻信任何人，用逻辑和证据来判断"
        ),
        "seer": (
            "你是预言家。你可以在每晚查验一名玩家的身份（狼人/好人）。\n"
            "策略要点：\n"
            "- 合理安排查验顺序，优先查验可疑玩家\n"
            "- 在白天适当时机公开你的查验结果，引导好人投票\n"
            "- 注意保护自己——过早暴露身份可能被狼人针对\n"
            "- 你的查验信息是好人阵营最重要的线索"
        ),
        "witch": (
            "你是女巫。你拥有一瓶解药和一瓶毒药，各限用一次。\n"
            "策略要点：\n"
            "- 解药：可以救活被狼人击杀的玩家。首夜使用价值高，但也可能浪费\n"
            "- 毒药：可以毒杀任意存活玩家。谨慎使用，避免误杀好人\n"
            "- 每晚只能使用一种药，需要根据局势判断优先级\n"
            "- 观察夜晚被击杀的玩家身份模式，推断狼人策略"
        ),
    }
    return strategies.get(role, "请根据你的角色做出合理决策。")


# ================================================================
# 决策指令文本
# ================================================================

def get_decision_instruction(action_type: str) -> str:
    """获取当前决策类型的指令文本

    参数:
        action_type: 决策类型（kill/verify/save/poison/speech/vote/last_words）

    返回:
        指令文本，包含输出格式要求
    """
    instructions = {
        "kill": (
            "【你的决策】选择今晚要击杀的目标。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数), "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": 3, \"reasoning\": \"3号玩家发言可疑，可能是预言家\"}"
        ),
        "verify": (
            "【你的决策】选择今晚要查验的目标玩家。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数), "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": 2, \"reasoning\": \"2号玩家的投票行为很可疑\"}"
        ),
        "save": (
            "【你的决策】今晚有人被狼人击杀，你要决定是否使用解药救人。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": true或false, "reasoning": "你的推理过程"}\n'
            "例如：{\"decision\": true, \"reasoning\": \"被击杀的可能是预言家，值得救\"}"
        ),
        "poison": (
            "【你的决策】你要决定是否使用毒药毒杀一名玩家。\n"
            "请以 JSON 格式输出你的决策：\n"
            '{"decision": 座位号(整数)或null, "reasoning": "你的推理过程"}\n'
            "decision为null表示不使用毒药。例如：{\"decision\": 4, \"reasoning\": \"4号很可能是狼人\"}"
        ),
        "speech": (
            "【你的决策】现在轮到你公开发言。\n"
            "请以 JSON 格式输出你的发言：\n"
            '{"decision": "你的发言文本(1-500字)", "reasoning": "你这样说的策略考量"}\n'
            "发言内容会公开展示给所有玩家。"
        ),
        "vote": (
            "【你的决策】现在进入投票环节，选择你要淘汰的目标。\n"
            "请以 JSON 格式输出你的投票：\n"
            '{"decision": 座位号(整数)或null, "reasoning": "你的推理过程"}\n'
            "decision为null表示弃票。例如：{\"decision\": 1, \"reasoning\": \"1号发言前后矛盾\"}"
        ),
        "last_words": (
            "【你的决策】你已被淘汰，请发表你的遗言。\n"
            "请以 JSON 格式输出你的遗言：\n"
            '{"decision": "你的遗言文本", "reasoning": "你的遗言策略"}\n'
            "遗言会公开展示，可以用来传递重要信息。"
        ),
    }
    return instructions.get(action_type, f"请做出 {action_type} 决策。")


# ================================================================
# 完整 Prompt 构建
# ================================================================

def build_agent_prompt(
    role: str,
    seat_number: int,
    action_type: str,
    game_context: dict[str, Any],
) -> list[BaseMessage]:
    """构建完整的 Agent Prompt 消息列表

    参数:
        role: 角色标识
        seat_number: Agent 座位号
        action_type: 当前决策类型
        game_context: 经 context_filter 过滤后的游戏上下文

    返回:
        LangChain 消息列表 [SystemMessage, HumanMessage]

    消息结构:
        SystemMessage: 通用规则 + 身份信息
        HumanMessage: 角色策略 + 当前局势 + 决策指令
    """
    # ─── System Prompt ───
    system_text = (
        f"你是一个狼人杀游戏的 AI 玩家。\n"
        f"你的座位号是 {seat_number} 号，你的角色是{_role_name_cn(role)}。\n"
        f"当前是第 {game_context.get('current_round', '?')} 轮。\n"
        f"存活玩家座位号: {game_context.get('alive_seats', [])}。\n\n"
        f"重要规则：\n"
        f"- 你只能通过游戏系统提供的信息来判断局势，不能使用任何外部信息\n"
        f"- 你的决策必须以 JSON 格式输出\n"
        f"- decision 字段是你的实际行动，reasoning 字段是你的推理过程\n"
    )

    # ─── 角色策略 + 局势 + 决策指令 ───
    strategy = get_role_strategy(role)
    instruction = get_decision_instruction(action_type)

    # 构建局势描述
    context_text = _format_game_context(game_context)

    human_text = (
        f"【角色策略】\n{strategy}\n\n"
        f"【当前局势】\n{context_text}\n\n"
        f"{instruction}"
    )

    return [
        SystemMessage(content=system_text),
        HumanMessage(content=human_text),
    ]


# ================================================================
# 内部辅助函数
# ================================================================

def _role_name_cn(role: str) -> str:
    """角色英文标识 → 中文名称"""
    names = {
        "werewolf": "狼人",
        "villager": "村民",
        "seer": "预言家",
        "witch": "女巫",
    }
    return names.get(role, role)


def _format_game_context(context: dict[str, Any]) -> str:
    """将游戏上下文格式化为可读的文本描述"""
    parts = []

    if "current_round" in context:
        parts.append(f"当前轮次: 第{context['current_round']}轮")

    if "alive_seats" in context:
        parts.append(f"存活玩家: {context['alive_seats']}")

    # 狼人同伴
    if "werewolf_companions" in context:
        parts.append(f"你的狼人同伴: {context['werewolf_companions']}号")

    # 预言家查验（完整历史）
    if "seer_results" in context and context["seer_results"]:
        for r in context["seer_results"]:
            result_cn = "狼人" if r.get("result") == "werewolf" else "好人"
            parts.append(f"第{r.get('round', '?')}轮查验结果: {r.get('target')}号是{result_cn}")

    # 女巫药水
    if "witch_save_used" in context:
        save_status = "已使用" if context["witch_save_used"] else "未使用"
        poison_status = "已使用" if context.get("witch_poison_used", False) else "未使用"
        parts.append(f"解药: {save_status}, 毒药: {poison_status}")

    # 被击杀者（女巫视角）
    if "night_kill_target" in context:
        parts.append(f"今晚被狼人击杀的是: {context['night_kill_target']}号")

    # ★★★ 昨晚情况（最关键的信息！AI 必须知道昨晚发生了什么）★★★
    # 只在白天决策时展示（发言/投票/遗言），夜晚行动时不展示（避免混淆“还未发生”和“平安夜”）
    action_type = context.get("action_type", "")
    if action_type in ("speech", "vote", "last_words"):
        night_deaths = context.get("night_deaths", None)
        if night_deaths is not None:
            if len(night_deaths) == 0:
                parts.append(f"\n【昨晚情况】昨晚是平安夜，无人死亡（可能是女巫用了解药）")
            else:
                parts.append(f"\n【昨晚情况】昨晚 {night_deaths}号 不幸离世（被狼人杀害）")

    # 跨轮历史（之前轮次的完整摘要）
    game_history = context.get("game_history", [])
    if game_history:
        parts.append("\n═══ 过往轮次回顾 ═══")
        for hist in game_history:
            rnd = hist.get("round", "?")
            parts.append(f"\n【第{rnd}轮】")
            # 夜晚死亡
            nd = hist.get("night_deaths", [])
            if nd:
                parts.append(f"  夜晚死亡: {nd}号")
            elif nd is not None and len(nd) == 0:
                parts.append(f"  夜晚: 平安夜")
            # 发言
            hist_speeches = hist.get("speeches", [])
            if hist_speeches:
                parts.append(f"  发言记录:")
                for s in hist_speeches:
                    # 不截断，完整展示
                    parts.append(f"    {s['seat']}号: {s['content']}")
            # 投票
            hist_votes = hist.get("votes", {})
            if hist_votes:
                vote_lines = [f"    {v}号→{t}号" if t else f"    {v}号→弃票" for v, t in hist_votes.items()]
                parts.append(f"  投票:\n" + "\n".join(vote_lines))
            # 淘汰
            elim = hist.get("eliminated_seat")
            if elim is not None:
                parts.append(f"  被淘汰: {elim}号")
        parts.append("\n═══ 过往回顾结束 ═══")

    # 当前轮发言（实时数据）
    speeches = context.get("speeches", [])
    if speeches:
        parts.append("\n【本轮已有发言】")
        for s in speeches:
            parts.append(f"  {s['seat']}号: {s['content']}")

    # 当前轮投票（实时数据）
    votes = context.get("votes", {})
    if votes:
        vote_parts = [f"{v}号→{t}号" if t else f"{v}号→弃票" for v, t in votes.items()]
        parts.append(f"本轮投票: {', '.join(vote_parts)}")

    # 玩家状态概览
    players = context.get("players", [])
    if players:
        alive_info = []
        dead_info = []
        for p in players:
            seat = p["seat_number"]
            if p["is_alive"]:
                alive_info.append(f"{seat}号")
            else:
                dead_info.append(f"{seat}号(第{p.get('death_round', '?')}轮死亡)")
        if alive_info:
            parts.append(f"存活: {', '.join(alive_info)}")
        if dead_info:
            parts.append(f"已淘汰: {', '.join(dead_info)}")

    return "\n".join(parts) if parts else "暂无额外信息"
