"""AI 狼人杀 — Agent 推理子图

职责：将 AI Agent 的推理过程封装为 4 步流水线
  ContextFilter → PromptBuild → LLMCall → DecisionParse

技术方案 §5.1 对应实现

调用链：GameFlowGraph(node) → run_agent(agent_state) → decision_dict

注意：P3 阶段使用同步函数实现（非 LangGraph StateGraph），
因为当前 4 步是严格顺序执行，无需条件分支。
后续如需加入重试循环或条件分支，可升级为 LangGraph 子图。
"""

import logging
import random
import time
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from app.agent.context_filter import filter_context
from app.agent.prompts import build_agent_prompt
from app.agent.decision_parser import parse_decision, validate_decision
from app.agent.llm import create_llm, MockWerewolfLLM

logger = logging.getLogger(__name__)


# ─── Agent 状态类型（字典约定） ──────────────────────────
# AgentState 不是 TypedDict（避免与 GameFlowState 耦合），而是 dict 约定
# 键: seat_number, role, player_name, action_type, game_context, llm
AgentState = dict[str, Any]


def run_agent(state: AgentState) -> dict[str, Any]:
    """运行 Agent 推理流水线（同步）

    参数:
        state: Agent 状态字典，包含:
            - seat_number: int — Agent 座位号
            - role: str — Agent 角色
            - player_name: str — Agent 显示名
            - action_type: str — 当前决策类型
            - game_context: dict — 完整游戏上下文（未过滤）
            - llm: BaseChatModel — LLM 实例（可选，默认 create_llm()）

    返回:
        决策结果字典:
        {
            "decision": 决策值,
            "reasoning": "推理过程",
            "is_fallback": bool,     — 是否降级为随机
            "latency_ms": int,       — 总耗时毫秒
        }
    """
    start_time = time.monotonic()

    seat = state["seat_number"]
    role = state["role"]
    action_type = state["action_type"]
    game_context = state["game_context"]

    # ─── Step 1: ContextFilter — 信息隔离 ───
    filtered = filter_context(game_context, seat, role, action_type)

    # ─── Step 2: PromptBuild — 构建 Prompt ───
    messages = build_agent_prompt(
        role=role,
        seat_number=seat,
        action_type=action_type,
        game_context=filtered,
    )

    # ─── Step 3: LLMCall — 调用模型 ───
    llm: BaseChatModel = state.get("llm") or create_llm()
    is_fallback = False

    # 如果是 MockWerewolfLLM，动态设置 decision_type
    if isinstance(llm, MockWerewolfLLM):
        llm.decision_type = action_type

    try:
        response = llm.invoke(messages)
        llm_text = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.warning(f"[Agent] {seat}号 LLM 调用失败: {e}，降级随机")
        llm_text = None
        is_fallback = True

    # ─── Step 4: DecisionParse — 解析 + 校验 ───
    parsed = parse_decision(llm_text) if llm_text else None

    if parsed is None:
        # 解析失败 → 降级随机决策
        logger.warning(f"[Agent] {seat}号 解析失败，降级随机决策")
        is_fallback = True
        parsed = _random_decision(action_type, filtered, seat)

    decision_value = parsed.get("decision")

    # 校验决策合法性
    alive_seats = filtered.get("alive_seats", [])
    werewolf_seats = filtered.get("werewolf_companions", [])
    if role == "werewolf":
        # 校验时加上自己的座位号到 werewolf_seats
        all_werewolf_seats = game_context.get("werewolf_seats", [])
    else:
        all_werewolf_seats = []

    valid, reason = validate_decision(
        decision=decision_value,
        action_type=action_type,
        alive_seats=alive_seats,
        own_seat=seat,
        werewolf_seats=all_werewolf_seats if action_type == "kill" else None,
    )

    if not valid:
        logger.warning(f"[Agent] {seat}号 决策不合法({reason})，降级随机")
        is_fallback = True
        parsed = _random_decision(action_type, filtered, seat)

    latency_ms = int((time.monotonic() - start_time) * 1000)

    result = {
        "decision": parsed.get("decision"),
        "reasoning": parsed.get("reasoning", ""),
        "is_fallback": is_fallback,
        "latency_ms": latency_ms,
    }

    logger.info(
        f"[Agent] {seat}号({role}) {action_type}: "
        f"decision={result['decision']} fallback={is_fallback} "
        f"latency={latency_ms}ms"
    )

    return result


# ================================================================
# 降级随机决策
# ================================================================

def _random_decision(
    action_type: str,
    context: dict[str, Any],
    own_seat: int,
) -> dict[str, Any]:
    """生成随机合法决策（降级用）

    参数:
        action_type: 决策类型
        context: 过滤后的上下文
        own_seat: 自己的座位号

    返回:
        {"decision": 随机值, "reasoning": "[降级] 随机决策"}
    """
    alive_seats = context.get("alive_seats", [])
    other_seats = [s for s in alive_seats if s != own_seat]

    match action_type:
        case "kill":
            # 排除狼人同伴
            companions = context.get("werewolf_companions", [])
            targets = [s for s in other_seats if s not in companions]
            decision = random.choice(targets) if targets else None
        case "verify":
            decision = random.choice(other_seats) if other_seats else None
        case "save":
            decision = random.choice([True, False])
        case "poison":
            decision = random.choice(other_seats + [None]) if other_seats else None
        case "speech":
            decision = f"（{own_seat}号自动发言：目前信息有限，我继续观察。）"
        case "vote":
            decision = random.choice(other_seats + [None]) if other_seats else None
        case "last_words":
            decision = f"（{own_seat}号自动遗言：希望好人阵营能获胜。）"
        case _:
            decision = None

    return {"decision": decision, "reasoning": "[降级] 随机决策"}
