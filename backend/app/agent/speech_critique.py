"""AI 狼人杀 — 发言批评-修订引擎（需求二：Speech Critique Loop）

把 AI 普通发言从 one-shot 升级为 draft → critique →（revise）最多一个回边，
专治身份穿帮与自相矛盾（FR-1）。critique/revise 任何异常一律回退草稿原文
（FR-3/FR-4），发言永远有产出。

隔离红线（P0）：critique 评审单含真实身份，只进 AgentLog，绝不产生任何
GameEvent / WS 广播 / replay（见技术方案 H 段）。

调用链：day_speech_node → call_agent_speech_critiqued → critique_and_revise
"""

import json
import logging
import re
from typing import Any

from app.agent.context_filter import filter_context
from app.agent.llm import create_llm
from app.agent.prompts import build_critique_prompt, build_revise_messages

logger = logging.getLogger(__name__)

# 触发修订的最低风险等级（FR-1/D6：仅 risk_level ≥ medium 触发 revise）
_REVISE_RISK_LEVELS = {"medium", "high"}
# 发言长度上限（对齐 agent_graph.py 现有发言截断 L311-315）
_SPEECH_MAX_LEN = 1000


def parse_critique(raw: Any) -> dict[str, Any]:
    """从审稿人 LLM 文本解析评审单 JSON。

    解析失败 / 字段缺失 → 返回 risk_level="none"（FR-1：宁可漏改，不可阻塞）。
    """
    empty = {"risk_level": "none", "issues": [], "fix_hint": ""}
    if not raw or not isinstance(raw, str) or not raw.strip():
        return empty

    data = _extract_json(raw.strip())
    if not isinstance(data, dict):
        return empty

    risk = data.get("risk_level")
    if risk not in ("high", "medium", "low", "none"):
        risk = "none"

    issues = data.get("issues")
    clean_issues: list[dict[str, str]] = []
    if isinstance(issues, list):
        for it in issues:
            if isinstance(it, dict):
                clean_issues.append({
                    "type": str(it.get("type", "?")),
                    "quote": str(it.get("quote", "")),
                    "why": str(it.get("why", "")),
                })

    fix_hint = data.get("fix_hint")
    return {
        "risk_level": risk,
        "issues": clean_issues,
        "fix_hint": str(fix_hint) if fix_hint else "",
    }


def _extract_json(text: str) -> dict | None:
    """从文本中提取第一个 JSON 对象（直接解析 → 代码块 → 花括号截取）。"""
    # 1) 直接解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    # 2) markdown 代码块
    md = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if md:
        try:
            obj = json.loads(md.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    # 3) 第一个 { 到最后一个 }
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        try:
            obj = json.loads(text[first:last + 1])
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def extract_own_history(filtered: dict[str, Any], seat: int) -> str:
    """从过滤上下文中抽出本人过往发言（供审稿维度②自相矛盾判断）。"""
    lines: list[str] = []
    # 跨轮历史
    for hist in filtered.get("game_history", []) or []:
        rnd = hist.get("round", "?")
        for s in hist.get("speeches", []) or []:
            if s.get("seat") == seat:
                lines.append(f"第{rnd}轮：{s.get('content', '')}")
    # 本轮已有发言（不含正在生成的草稿）
    for s in filtered.get("speeches", []) or []:
        if s.get("seat") == seat:
            lines.append(f"本轮：{s.get('content', '')}")
    return "\n".join(lines)


def summarize_review(review: dict[str, Any]) -> str:
    """评审单 → AgentLog.critique_result 摘要 JSON（risk_level + issue 类型列表 + fix_hint）。"""
    return json.dumps({
        "risk_level": review.get("risk_level", "none"),
        "issue_types": [i.get("type") for i in review.get("issues", []) if isinstance(i, dict)],
        "fix_hint": review.get("fix_hint", ""),
    }, ensure_ascii=False)


def _clean_text(text: Any) -> str:
    """清理修订稿：剥离 markdown 代码块包裹与首尾空白（对齐 agent_graph._clean_speech_text）。"""
    if not isinstance(text, str):
        return ""
    t = text.strip()
    md = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", t, re.DOTALL)
    if md:
        t = md.group(1).strip()
    return t


def _apply_speech_safeguards(revised_text: str, draft: str) -> tuple[str, bool]:
    """修订稿全链路兜底（FR-3/验收5）。返回 (final_text, is_fallback)。

    - 空稿/纯空白 → 回退草稿，is_fallback=True
    - 超长（>1000）→ 截断前 1000 字，is_fallback=False（内容是 AI 原始输出）
    - 合法非空 → 修订稿，is_fallback=False
    """
    if not revised_text or not revised_text.strip():
        return draft, True
    if len(revised_text) > _SPEECH_MAX_LEN:
        return revised_text[:_SPEECH_MAX_LEN], False
    return revised_text, False


async def critique_and_revise(
    game_state: dict[str, Any],
    player: dict[str, Any],
    action_type: str,
    draft: str,
) -> dict[str, Any]:
    """对发言草稿执行 critique →（revise）。全链路异常一律回退草稿（FR-3/FR-4）。

    返回: {"final_text": str, "critique_result": str|None, "revised": bool, "is_fallback": bool}
    """
    seat = player["seat_number"]
    role = getattr(player["role"], "value", player["role"])
    filtered = filter_context(game_state, seat, role, action_type)

    # ─── critique：小模型审稿（隔离上下文内部运行，产物只进 AgentLog）───
    try:
        cmsgs = build_critique_prompt(
            role, seat, draft,
            extract_own_history(filtered, seat),
            filtered.get("werewolf_companions"),
        )
        cllm = create_llm(seat_number=seat, action_type="critique", game_id=game_state.get("game_id"))
        cresp = await cllm.ainvoke(cmsgs)
        craw = cresp.content if hasattr(cresp, "content") else str(cresp)
        review = parse_critique(craw)
    except Exception as e:
        logger.warning(f"[Critique] {seat}号审稿异常，草稿直接过: {e}")
        return {"final_text": draft, "critique_result": None, "revised": False, "is_fallback": True}

    # ─── 仅 risk_level ≥ medium 触发修订（FR-1/D6）───
    if review["risk_level"] not in _REVISE_RISK_LEVELS:
        return {
            "final_text": draft,
            "critique_result": summarize_review(review),
            "revised": False,
            "is_fallback": False,
        }

    # ─── revise：大模型（本我），单次，修订稿不再过 critique（FR-3）───
    try:
        rmsgs = build_revise_messages(role, seat, filtered, draft, review)
        rllm = create_llm(seat_number=seat, action_type="speech", game_id=game_state.get("game_id"))
        rresp = await rllm.ainvoke(rmsgs)
        rraw = rresp.content if hasattr(rresp, "content") else str(rresp)
        revised_text = _clean_text(rraw)
    except Exception as e:
        logger.warning(f"[Critique] {seat}号修订异常，回退草稿: {e}")
        return {
            "final_text": draft,
            "critique_result": summarize_review(review),
            "revised": False,
            "is_fallback": True,
        }

    final, is_fallback = _apply_speech_safeguards(revised_text, draft)
    return {
        "final_text": final,
        "critique_result": summarize_review(review),
        "revised": not is_fallback,
        "is_fallback": is_fallback,
    }
