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
import queue
import random
import re
import threading
import time
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.agent.context_filter import filter_context
from app.agent.decision_parser import parse_decision, validate_decision
from app.agent.llm import MockWerewolfLLM, create_llm
from app.agent.prompts import build_agent_prompt

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


# ─── B4: 队列式 AgentLog 持久化 ──────────────────────────
# 单后台线程消费队列，避免每条日志启动一个守护线程导致进程退出瞬间日志丢失
_log_queue: queue.Queue = queue.Queue(maxsize=10000)
_log_worker_started = False
_log_worker_thread: threading.Thread | None = None
_log_shutdown = threading.Event()


def _start_log_worker() -> None:
    """启动单日志消费后台线程（只启动一次）"""
    global _log_worker_started, _log_worker_thread
    if _log_worker_started:
        return
    _log_worker_started = True
    _log_worker_thread = threading.Thread(target=_log_worker, daemon=True, name="agent-log-writer")
    _log_worker_thread.start()


def _log_worker() -> None:
    """后台线程：从队列批量消费并写入 DB"""
    from sqlalchemy.orm import Session

    from app.models.game import AgentLog

    batch: list = []
    batch_size = 10

    while not (_log_shutdown.is_set() and _log_queue.empty()):
        try:
            entry = _log_queue.get(timeout=1.0)
        except queue.Empty:
            # 超时或关闭时刷写剩余
            if batch:
                _flush_batch(batch, AgentLog, Session)
                batch.clear()
            continue

        batch.append(entry)
        if len(batch) >= batch_size:
            _flush_batch(batch, AgentLog, Session)
            batch.clear()

    # 最终刷写
    if batch:
        _flush_batch(batch, AgentLog, Session)


def _flush_batch(batch: list, AgentLog, Session) -> None:
    try:
        engine = _get_sync_engine()
        with Session(engine) as session:
            for item in batch:
                prompt_text = "\n\n".join(
                    f"[{type(m).__name__}] {m.content}"
                    for m in item["prompt_messages"]
                    if hasattr(m, "content")
                )
                log = AgentLog(
                    game_id=item["game_id"],
                    round_number=item["round_number"],
                    seat_number=item["seat_number"],
                    action_type=item["action_type"],
                    context_json=json.dumps(item["filtered_context"], ensure_ascii=False, default=str),
                    prompt_text=prompt_text,
                    llm_raw_output=item["llm_raw_output"],
                    parsed_decision=json.dumps(item["parsed_decision"], ensure_ascii=False, default=str) if item["parsed_decision"] else None,
                    is_fallback=item["is_fallback"],
                    latency_ms=item["latency_ms"],
                    prompt_tokens=item.get("prompt_tokens"),
                    completion_tokens=item.get("completion_tokens"),
                    model_name=item.get("model_name"),
                    critique_result=item.get("critique_result"),
                    revised=item.get("revised"),
                )
                session.add(log)
            session.commit()
    except Exception as e:
        logger.warning(f"[Agent] AgentLog 批量持久化失败（不影响主链）: {e}")


def _flush_log_queue() -> None:
    """B4: 应用关闭时刷写剩余日志"""
    _log_shutdown.set()
    if _log_worker_thread:
        _log_worker_thread.join(timeout=5)


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
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    model_name: str | None = None,
    critique_result: str | None = None,
    revised: bool | None = None,
) -> None:
    """将 AI 决策审计日志持久化到 agent_logs 表。

    B4: 改为入队操作，由单后台线程批量消费写入 DB，避免进程退出瞬间日志丢失。
    """
    _start_log_worker()
    try:
        _log_queue.put_nowait({
            "game_id": game_id,
            "round_number": round_number,
            "seat_number": seat_number,
            "action_type": action_type,
            "filtered_context": filtered_context,
            "prompt_messages": prompt_messages,
            "llm_raw_output": llm_raw_output,
            "parsed_decision": parsed_decision,
            "is_fallback": is_fallback,
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "model_name": model_name,
            "critique_result": critique_result,
            "revised": revised,
        })
    except queue.Full:
        logger.warning("[Agent] AgentLog 队列已满，丢弃本条")


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

    # ─── Step 3: LLMCall — 调用模型（按决策类型路由到不同模型，阶段 3） ───
    llm: BaseChatModel = state.get("llm") or create_llm(seat_number=seat, action_type=action_type)
    is_fallback = False

    # 如果是 MockWerewolfLLM，动态设置 decision_type
    if isinstance(llm, MockWerewolfLLM):
        llm.decision_type = action_type

    # 提取 token 用量和模型名（在 LLM 调用成功后）
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    resolved_model: str | None = None

    # L2: 失败重试 1 次（指数退避），仍失败才走 fallback
    llm_text = None
    for attempt in range(2):
        try:
            response = llm.invoke(messages)
            llm_text = response.content if hasattr(response, "content") else str(response)

            # 从 LangChain 响应提取 usage（标准字段）
            usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("usage")
            if usage:
                if isinstance(usage, dict):
                    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
                    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
                elif hasattr(usage, "prompt_tokens"):
                    prompt_tokens = getattr(usage, "prompt_tokens", None)
                    completion_tokens = getattr(usage, "completion_tokens", None)

            # 记录实际使用的模型名
            resolved_model = getattr(llm, "model_name", None) or getattr(llm, "model", None)
            if resolved_model and not isinstance(resolved_model, str):
                resolved_model = str(resolved_model)
            break  # 成功，跳出重试循环
        except Exception as e:
            if attempt == 0:
                logger.warning(f"[Agent] {seat}号 LLM 第1次调用失败: {e}，1s 后重试")
                time.sleep(1)  # 指数退避：第1次重试等 1s
            else:
                logger.warning(f"[Agent] {seat}号 LLM 第2次调用失败: {e}，降级随机")
                is_fallback = True
        llm_text = None
        is_fallback = True

    # ─── Step 4: DecisionParse — 解析 + 校验 ───
    parsed = parse_decision(llm_text) if llm_text else None

    if parsed is None:
        if action_type in ("speech", "last_words") and isinstance(llm_text, str) and llm_text.strip():
            # 发言/遗言：prompt 要求直接输出文本，解析失败时用 LLM 原文（兼容模型仍输出 JSON 的惯性）
            cleaned = _clean_speech_text(llm_text)
            parsed = {"decision": cleaned, "reasoning": "(直接文本输出)"}
            logger.info(f"[Agent] {seat}号 {action_type} 直接使用 LLM 原文（非 JSON 输出）")
        else:
            # 解析失败 → 降级随机决策
            logger.warning(f"[Agent] {seat}号 解析失败，降级随机决策")
            is_fallback = True
            parsed = _random_decision(action_type, filtered, seat)

    decision_value = parsed.get("decision")

    # 校验决策合法性
    alive_seats = filtered.get("alive_seats", [])
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
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model_name=resolved_model,
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

def _clean_speech_text(text: str) -> str:
    """清理发言/遗言原文：去掉 markdown 代码块包裹与首尾空白

    模型可能惯性输出 ```json ... ``` 包裹的文本，剥离后保留正文。
    """
    t = text.strip()
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", t, re.DOTALL)
    if md_match:
        t = md_match.group(1).strip()
    return t


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
