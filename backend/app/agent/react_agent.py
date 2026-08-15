"""AI 狼人杀 — ReAct Agent 子图

LangGraph 子图实现 ReAct 循环：
  agent_node → [tool_calls] → tool_node → ToolMessage 回填 → agent_node (循环)
  agent_node → [final_decision JSON] → END

三个"真 ReAct"要点：
1. Observation 必须回填 messages（ToolMessage）
2. Thought 显式产出：每轮要求输出 reasoning 文本
3. 终止双保险：LLM 自主决策为主出口，max_iterations=3 强制出图
"""

import json
import logging
import time
from typing import Any, Literal, TypedDict

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)

# ─── ReAct 状态 ───


class ReActState(TypedDict):
    messages: list[BaseMessage]
    game_id: str
    db_path: str
    seat_number: int
    role: str
    action_type: str
    game_context: dict  # 完整上下文（用于 fallback）
    iteration: int
    max_iterations: int
    # 输出
    final_decision: dict | None
    agent_steps: list[dict]  # ReAct 轨迹记录
    is_fallback: bool
    latency_ms: int


# ─── Agent 节点 ───


def _build_react_system_prompt(action_type: str, seat: int, role: str) -> str:
    """ReAct 专用 system prompt，强调 Thought-Action-Observation 循环"""
    return (
        f"你是狼人杀游戏中的 {seat}号 {role} 玩家。\n"
        f"当前需要做出的决策类型：{action_type}\n\n"
        f"你必须使用以下工具辅助决策：\n"
        f"- query_vote_history: 查询历史票型\n"
        f"- query_speech: 查询历史发言\n"
        f"- query_death_history: 查询死亡与死因\n\n"
        f"决策流程：\n"
        f"1. 先用 reasoning 分析当前局势（Thought）\n"
        f"2. 如果需要更多信息，调用工具查询（Action）\n"
        f"3. 根据工具返回结果继续推理（Observation）\n"
        f"4. 当你有足够信息做出决策时，输出最终决策 JSON\n\n"
        f"最终决策 JSON 格式：\n"
        f'{{"decision": <值>, "reasoning": "<推理过程>"}}\n'
        f'decision 字段根据你的行动类型应该是座位号(int)、布尔值或文本。'
        f"只在你确信有足够信息时才输出最终决策 JSON，否则继续调用工具。"
    )


def _build_initial_messages(
    seat: int, role: str, action_type: str, context: dict
) -> list[BaseMessage]:
    """构建 ReAct 初始消息"""

    # 复用现有 prompt 构建（但要求 LLM 用工具查询而非依赖全量历史）
    system_text = _build_react_system_prompt(action_type, seat, role)

    # 构建局势描述（截断历史，强制 LLM 用工具查询）
    context_lines = []
    context_lines.append(f"当前轮次: 第{context.get('current_round', '?')}轮")
    context_lines.append(f"存活玩家: {context.get('alive_seats', [])}")
    if "night_deaths" in context:
        context_lines.append(f"昨晚死亡: {context['night_deaths']}")

    # 不塞入全量历史和当前轮发言，强制 LLM 用工具查询
    human_text = (
        "【当前局势】\n" + "\n".join(context_lines) + "\n\n"
        "请使用工具查询历史票型、发言和死亡记录来辅助你的决策。\n"
        "先分析局势（Thought），再决定是否调用工具。"
    )

    return [
        SystemMessage(content=system_text),
        HumanMessage(content=human_text),
    ]


async def agent_node(state: ReActState) -> dict:
    """ReAct Agent 节点：LLM 推理 + 可能的 tool_calls"""
    from app.agent.llm import create_llm
    from app.agent.react_tools import REACT_TOOLS

    seat = state["seat_number"]
    action_type = state["action_type"]

    # 阶段 3 路由：决策类走小模型
    llm = create_llm(seat_number=seat, action_type=action_type)
    llm_with_tools = llm.bind_tools(REACT_TOOLS)

    messages = list(state["messages"])
    iteration = state["iteration"]
    agent_steps = list(state.get("agent_steps", []))

    # 记录 Thought 步骤
    agent_steps.append({
        "step_index": len(agent_steps),
        "step_type": "thought",
        "content": f"--- ReAct 第 {iteration + 1} 轮 ---",
    })

    try:
        response = await llm_with_tools.ainvoke(messages)
    except Exception as e:
        logger.warning(f"[ReAct] {seat}号 LLM 调用失败: {e}")
        return {
            "final_decision": {"decision": None, "reasoning": f"LLM 调用失败: {e}"},
            "agent_steps": agent_steps,
            "is_fallback": True,
        }

    # 检查是否有 tool_calls
    tool_calls = getattr(response, "tool_calls", None)

    if tool_calls:
        # 记录 tool_calls
        for tc in tool_calls:
            agent_steps.append({
                "step_index": len(agent_steps),
                "step_type": "tool_call",
                "content": f"调用 {tc['name']}: {json.dumps(tc['args'], ensure_ascii=False)}",
            })
        return {"messages": [response], "agent_steps": agent_steps, "iteration": iteration + 1}

    # 无 tool_calls → 尝试解析最终决策
    llm_text = response.content if hasattr(response, "content") else str(response)
    agent_steps.append({
        "step_index": len(agent_steps),
        "step_type": "final",
        "content": llm_text,
    })

    # 尝试解析 JSON
    try:
        decision = json.loads(llm_text) if isinstance(llm_text, str) else llm_text
        if isinstance(decision, dict) and "decision" in decision:
            return {"final_decision": decision, "agent_steps": agent_steps}
    except (json.JSONDecodeError, TypeError):
        pass

    # 解析失败 → 非 JSON 文本
    return {
        "final_decision": {"decision": llm_text, "reasoning": "(非 JSON 输出)"},
        "agent_steps": agent_steps,
    }


# ─── Tool 节点 ───


async def tool_node(state: ReActState) -> dict:
    """执行工具调用并回填 ToolMessage"""
    from app.agent.react_tools import REACT_TOOLS

    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", [])
    if not tool_calls:
        return {"messages": []}

    tool_map = {t.name: t for t in REACT_TOOLS}
    results = []

    for tc in tool_calls:
        tool_name = tc["name"]
        tool_args = dict(tc["args"])

        # 注入 game_id/db_path/seat_number/role
        tool_args["game_id"] = state["game_id"]
        tool_args["db_path"] = state["db_path"]
        tool_args["seat_number"] = state["seat_number"]
        tool_args["role"] = state["role"]

        tool_func = tool_map.get(tool_name)
        if tool_func:
            try:
                result = tool_func.invoke(tool_args)
                observation = result if isinstance(result, str) else str(result)
            except Exception as e:
                observation = f"工具调用失败: {e}"
        else:
            observation = f"未知工具: {tool_name}"

        # 记录 Observation
        state["agent_steps"].append({
            "step_index": len(state["agent_steps"]),
            "step_type": "observation",
            "content": observation[:300] if len(observation) > 300 else observation,
        })

        results.append(ToolMessage(content=observation, tool_call_id=tc["id"]))

    return {"messages": results}


# ─── 路由函数 ───


def route_after_agent(state: ReActState) -> Literal["tools", "end"]:
    """判断是否还有 tool_calls 需要执行"""
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if tool_calls:
        return "tools"
    return "end"


def route_after_tools(state: ReActState) -> Literal["agent", "fallback"]:
    """工具执行后：检查是否超过最大迭代次数"""
    if state["iteration"] >= state.get("max_iterations", 3):
        return "fallback"
    return "agent"


# ─── 构建子图 ───


def build_react_graph() -> Any:
    """构建 ReAct 子图

    结构：
      START → agent → {tool_calls? → tools → {iter < max? → agent : fallback} : END}
    """
    graph = StateGraph(ReActState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)

    graph.add_edge(START, "agent")

    # agent → tools 或 end
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "end": END},
    )

    # tools → agent 或 fallback
    graph.add_conditional_edges(
        "tools",
        route_after_tools,
        {"agent": "agent", "fallback": END},
    )

    return graph.compile()


# ─── 公开入口 ───


async def run_react_agent(
    game_id: str,
    db_path: str,
    seat_number: int,
    role: str,
    action_type: str,
    game_context: dict,
    max_iterations: int = 3,
) -> dict:
    """运行 ReAct Agent 决策

    返回:
        {
            "decision": <值>,
            "reasoning": "<推理过程>",
            "is_fallback": bool,
            "latency_ms": int,
            "agent_steps": [...],
        }
    """
    start_time = time.monotonic()

    messages = _build_initial_messages(seat_number, role, action_type, game_context)

    react_graph = build_react_graph()
    initial_state: ReActState = {
        "messages": messages,
        "game_id": game_id,
        "db_path": db_path,
        "seat_number": seat_number,
        "role": role,
        "action_type": action_type,
        "game_context": game_context,
        "iteration": 0,
        "max_iterations": max_iterations,
        "final_decision": None,
        "agent_steps": [],
        "is_fallback": False,
        "latency_ms": 0,
    }

    try:
        result = await react_graph.ainvoke(initial_state)
    except Exception as e:
        logger.error(f"[ReAct] 子图执行失败: {e}", exc_info=True)
        # Fallback：走全量单次调用路径
        from app.agent.context_filter import filter_context
        from app.agent.llm import create_llm
        from app.agent.prompts import build_agent_prompt

        filtered = filter_context(game_context, seat_number, role, action_type)
        msgs = build_agent_prompt(role=role, seat_number=seat_number,
                                  action_type=action_type, game_context=filtered)
        llm = create_llm(seat_number=seat_number, action_type=action_type)
        try:
            resp = await llm.ainvoke(msgs)
            text = resp.content if hasattr(resp, "content") else str(resp)
            decision = json.loads(text) if isinstance(text, str) else None
        except Exception:
            decision = None

        latency_ms = int((time.monotonic() - start_time) * 1000)
        return {
            "decision": decision.get("decision") if isinstance(decision, dict) else None,
            "reasoning": decision.get("reasoning", "ReAct fallback") if isinstance(decision, dict) else "ReAct fallback",
            "is_fallback": True,
            "latency_ms": latency_ms,
            "agent_steps": [{"step_index": 0, "step_type": "final", "content": "ReAct 子图异常，走全量兜底"}],
        }

    final = result.get("final_decision", {})
    latency_ms = int((time.monotonic() - start_time) * 1000)
    agent_steps = result.get("agent_steps", [])

    if isinstance(final, dict) and "decision" in final:
        decision_value = final["decision"]
        reasoning = final.get("reasoning", "")
    else:
        decision_value = None
        reasoning = "未能解析决策 JSON"

    return {
        "decision": decision_value,
        "reasoning": reasoning,
        "is_fallback": result.get("is_fallback", False),
        "latency_ms": latency_ms,
        "agent_steps": agent_steps,
    }
