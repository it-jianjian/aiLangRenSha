"""AI 狼人杀 — 决策解析器

职责：
1. parse_decision() — 从 LLM 原始文本输出中提取结构化决策
2. validate_decision() — 校验决策是否合法（符合游戏规则）

技术方案 §5.5 对应实现

解析流程：LLM 输出 → 提取 JSON 块 → json.loads → dict → 合法性校验
重试策略：解析失败 → 修正 Prompt(附合法示例) → 重新调用 LLM → 仍失败 → 随机决策

调用链：AgentGraph(DecisionParseNode) → parse_decision → validate_decision
"""

import json
import re
from typing import Any, Optional


def parse_decision(llm_output: str) -> Optional[dict[str, Any]]:
    """从 LLM 文本输出中解析结构化决策

    参数:
        llm_output: LLM 的原始文本输出

    返回:
        解析成功: {"decision": 值, "reasoning": "推理过程"}
        解析失败: None

    解析策略:
        1. 直接尝试 json.loads（标准 JSON）
        2. 从 Markdown 代码块中提取 JSON
        3. 用正则从文本中提取第一个 {...} 块
        4. 全部失败 → 返回 None
    """
    if not llm_output or not llm_output.strip():
        return None

    text = llm_output.strip()

    # ─── 策略 1: 直接解析 ───
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "decision" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    # ─── 策略 2: 从 Markdown 代码块中提取 ───
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if md_match:
        try:
            data = json.loads(md_match.group(1).strip())
            if isinstance(data, dict) and "decision" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    # ─── 策畧 3: 正则提取 {...}（支持嵌套花括号）───
    # 从最外层花括号开始匹配，允许内部包含嵌套 {}
    brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*"decision"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if not brace_match:
        # 备用：找最后一个 } 之前的所有内容
        first_brace = text.find('{')
        last_brace = text.rfind('}')
        if first_brace != -1 and last_brace > first_brace:
            brace_match = text[first_brace:last_brace + 1]
    if brace_match:
        try:
            json_str = brace_match if isinstance(brace_match, str) else brace_match.group()
            data = json.loads(json_str)
            if isinstance(data, dict) and "decision" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    
    # ─── 策畧 4: 宽松提取（尝试提取所有 JSON 块）───
    for match in re.finditer(r'\{.*\}', text, re.DOTALL):
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and "decision" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def validate_decision(
    decision: Any,
    action_type: str,
    alive_seats: list[int],
    own_seat: int,
    werewolf_seats: Optional[list[int]] = None,
) -> tuple[bool, str]:
    """校验决策是否合法

    参数:
        decision: 解析后的 decision 值
        action_type: 决策类型（kill/verify/save/poison/speech/vote/last_words）
        alive_seats: 当前存活的座位号列表
        own_seat: Agent 自己的座位号
        werewolf_seats: 狼人座位号列表（仅 kill 时需要）

    返回:
        (is_valid, reason) 元组
        - is_valid=True: 决策合法
        - is_valid=False, reason="原因": 决策不合法
    """

    # ─── kill: 击杀目标 ───
    if action_type == "kill":
        if not isinstance(decision, int):
            return False, "击杀目标必须是整数座位号"
        if decision == own_seat:
            return False, "不能击杀自己"
        if decision not in alive_seats:
            return False, "目标不在存活玩家列表中"
        if werewolf_seats and decision in werewolf_seats:
            return False, "不能击杀狼人同伴"
        return True, ""

    # ─── verify: 查验目标 ───
    if action_type == "verify":
        if not isinstance(decision, int):
            return False, "查验目标必须是整数座位号"
        if decision == own_seat:
            return False, "不能查验自己"
        if decision not in alive_seats:
            return False, "目标不在存活玩家列表中"
        return True, ""

    # ─── save: 解药 ───
    if action_type == "save":
        if not isinstance(decision, bool):
            return False, "解药决策必须是布尔值 (true/false)"
        return True, ""

    # ─── poison: 毒药 ───
    if action_type == "poison":
        if decision is None:
            return True, ""  # 不使用毒药是合法的
        if not isinstance(decision, int):
            return False, "毒药目标必须是整数座位号或 null"
        if decision == own_seat:
            return False, "不能对自己使用毒药"
        if decision not in alive_seats:
            return False, "目标不在存活玩家列表中"
        return True, ""

    # ─── speech: 发言 ───
    if action_type == "speech":
        if not isinstance(decision, str):
            return False, "发言必须是文本字符串"
        if len(decision.strip()) == 0:
            return False, "发言不能为空"
        if len(decision) > 500:
            return False, "发言不能超过500字"
        return True, ""

    # ─── vote: 投票 ───
    if action_type == "vote":
        if decision is None:
            return True, ""  # 弃票合法
        if not isinstance(decision, int):
            return False, "投票目标必须是整数座位号或 null"
        if decision == own_seat:
            return False, "不能投自己"
        if decision not in alive_seats:
            return False, "目标不在存活玩家列表中"
        return True, ""

    # ─── last_words: 遗言 ───
    if action_type == "last_words":
        if not isinstance(decision, str):
            return False, "遗言必须是文本字符串"
        if len(decision.strip()) == 0:
            return False, "遗言不能为空"
        return True, ""

    return False, f"未知决策类型: {action_type}"
