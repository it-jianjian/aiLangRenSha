"""AI 狼人杀 — Agent 推理子图

职责：将 AI Agent 的推理过程封装为 4 步流水线
  ContextFilter → PromptBuild → LLMCall → DecisionParse

技术方案 §5.1 对应实现

调用链：GameFlowGraph(node) → run_agent(agent_state) → decision_dict

注意：P3 阶段使用同步函数实现（非 LangGraph StateGraph），
因为当前 4 步是严格顺序执行，无需条件分支。
后续如需加入重试循环或条件分支，可升级为 LangGraph 子图。
"""

import json
import logging
import random
import threading
import time
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from app.agent.context_filter import filter_context
from app.agent.prompts import build_agent_prompt
from app.agent.decision_parser import parse_decision, validate_decision
from app.agent.llm import create_llm, MockWerewolfLLM

logger = logging.getLogger(__name__)

# ─── 同步引擎单例（用于 AgentLog 持久化） ───────────────────
_sync_engine = None


def _get_sync_engine():
    """获取同步 SQLAlchemy 引擎，从异步 URL 转换而来。

    AgentLog 持久化在同步上下文中执行（run_agent 是同步函数），
    因此需要一个同步引擎来写入数据库。
    """
    global _sync_engine
    if _sync_engine is not None:
        return _sync_engine

    from app.config import get_settings
    settings = get_settings()
    # 将 async URL 转为 sync URL
    sync_url = settings.database_url.replace("+aiosqlite", "").replace("+aiomysql", "+pymysql")
    from sqlalchemy import create_engine
    _sync_engine = create_engine(
        sync_url,
        connect_args={"check_same_thread": False} if "sqlite" in sync_url else {},
    )
    return _sync_engine


def _persist_agent_log(
    game_id: str,
    round_number: int,
    seat_number: int,
    action_type: str,
    filtered_context: dict[str, Any],
    prompt_messages: list,
    llm_raw_output: str | None,
    parsed_decision: dict[str, Any] | None,
    is_fallback: bool,
    latency_ms: int,
) -> None:
    """将 AI 决策审计日志持久化到 agent_logs 表。

    记录过滤后上下文、实际 Prompt、LLM 原始输出、解析结果和 fallback 标记。
    通过后台守护线程执行，不阻塞事件循环。
    """
    # 在后台守护线程中执行同步 DB 写，避免阻塞 asyncio 事件循环
    def _write_log():
        try:
            from app.models.game import AgentLog
            from sqlalchemy.orm import Session

            engine = _get_sync_engine()

            # 将 LangChain 消息列表序列化为文本
            prompt_text = "\n\n".join(
                f"[{type(m).__name__}] {m.content}"
                for m in prompt_messages
                if hasattr(m, "content")
            )

            log_entry = AgentLog(
                game_id=game_id,
                round_number=round_number,
                seat_number=seat_number,
                action_type=action_type,
                context_json=json.dumps(filtered_context, ensure_ascii=False, default=str),
                prompt_text=prompt_text,
                llm_raw_output=llm_raw_output,
                parsed_decision=json.dumps(parsed_decision, ensure_ascii=False, default=str) if parsed_decision else None,
                is_fallback=is_fallback,
                latency_ms=latency_ms,
            )

            with Session(engine) as session:
                session.add(log_entry)
                session.commit()
        except Exception as e:
            logger.warning(f"[Agent] AgentLog 持久化失败（不影响主链）: {e}")

    threading.Thread(target=_write_log, daemon=True).start()


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

    # ─── Step 3: LLMCall — 调用模型（按座位号路由到对应模型） ───
    llm: BaseChatModel = state.get("llm") or create_llm(seat_number=seat)
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
        allowed_target_seats=filtered.get("allowed_target_seats"),
        can_skip=bool(filtered.get("can_skip", False)),
    )

    if not valid:
        # 发言/遗言超长时截断保留原内容，而非替换为通用废话
        if action_type in ("speech", "last_words") and isinstance(decision_value, str) and len(decision_value) > 1000:
            logger.warning(f"[Agent] {seat}号 发言超长({len(decision_value)}字)，截断保留前1000字")
            decision_value = decision_value[:1000]
            parsed = {"decision": decision_value, "reasoning": parsed.get("reasoning", "") + " [截断]"}
            is_fallback = False  # 截断不算降级，内容是 AI 原始输出
        else:
            logger.warning(f"[Agent] {seat}号 决策不合法({reason})，降级随机")
            is_fallback = True
            parsed = _random_decision(action_type, filtered, seat)
            fallback_valid, fallback_reason = validate_decision(
                decision=parsed.get("decision"),
                action_type=action_type,
                alive_seats=alive_seats,
                own_seat=seat,
                werewolf_seats=all_werewolf_seats if action_type == "kill" else None,
                allowed_target_seats=filtered.get("allowed_target_seats"),
                can_skip=bool(filtered.get("can_skip", False)),
            )
            if not fallback_valid:
                logger.warning(f"[Agent] {seat}号 fallback 仍不合法({fallback_reason})，强制空决策")
                parsed = {"decision": None, "reasoning": f"[降级] 无合法目标：{fallback_reason}"}

    latency_ms = int((time.monotonic() - start_time) * 1000)

    # ─── 持久化 AgentLog 审计记录 ───
    _persist_agent_log(
        game_id=game_context.get("game_id", ""),
        round_number=game_context.get("current_round", 0),
        seat_number=seat,
        action_type=action_type,
        filtered_context=filtered,
        prompt_messages=messages,
        llm_raw_output=llm_text,
        parsed_decision=parsed,
        is_fallback=is_fallback,
        latency_ms=latency_ms,
    )

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
    allowed_targets = list(context.get("allowed_target_seats") or [])
    other_seats = [s for s in alive_seats if s != own_seat]

    match action_type:
        case "kill":
            # 排除狼人同伴
            companions = context.get("werewolf_companions", [])
            targets = allowed_targets or [s for s in other_seats if s not in companions]
            decision = targets[0] if targets else None
        case "verify":
            targets = allowed_targets or other_seats
            decision = targets[0] if targets else None
        case "save":
            decision = random.choice([True, False])
        case "poison":
            targets = allowed_targets or other_seats
            decision = targets[0] if targets else None
        case "speech":
            decision = f"（{own_seat}号自动发言：目前信息有限，我继续观察。）"
        case "vote":
            targets = allowed_targets or other_seats
            decision = targets[0] if targets else None
        case "last_words":
            decision = f"（{own_seat}号自动遗言：希望好人阵营能获胜。）"
        case "guard":
            decision = allowed_targets[0] if allowed_targets else None
        case "hunter_shoot":
            decision = allowed_targets[0] if allowed_targets else None
        case _:
            decision = None

    return {"decision": decision, "reasoning": "[降级] 随机决策"}
