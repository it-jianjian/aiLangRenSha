"""AI 狼人杀 — 复盘引擎（胜率曲线组装 + AI 点评生成）

两块能力：
1. 胜率曲线：纯规则实时计算（win_probability.build_win_curve），永远可用、无需 LLM；
2. AI 复盘点评：按需调 LLM 生成 MVP/关键转折/最佳最差操作/总评，缓存进 game_reviews 表。

隔离红线（P0）：复盘为对局结束后的上帝视角，可揭示全部身份，但素材排除
werewolf_negotiation（狼队协商）原始内容，与 replay 一致。

全链路 fallback：LLM 异常/解析失败 → insight=None, is_fallback=True，
胜率曲线照常返回（点评永远不阻塞曲线）。

调用链：
  review_router.get_review    → build_review_data（曲线 + 已缓存点评）
  review_router.generate      → generate_review（LLM 生成 + 写缓存）
"""

import json
import logging
from datetime import datetime
from itertools import groupby
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.llm import create_llm
from app.agent.prompts import build_review_prompt
from app.agent.speech_critique import _extract_json
from app.config import get_settings
from app.models.game import ChatMessage, Game, GameEvent, GameReview, Vote
from app.services.win_probability import build_win_curve, find_turning_points

logger = logging.getLogger(__name__)

# 隔离红线：狼队协商原始内容绝不进入复盘素材
_EXCLUDED_EVENT_TYPES = {"werewolf_negotiation"}
# 事件时间线中跳过的噪音/冗余事件（发言走 ChatMessage，投票走 Vote 表）
_SKIP_EVENT_TYPES = {
    "speech", "last_words", "pk_speech", "phase_change",
    "victory_check", "role_assign", "night_settle", "vote", "pk_vote", "timeout",
}
# 发言素材单条截断长度
_SPEECH_SNIPPET_LEN = 120

_ROLE_CN = {
    "werewolf": "狼人", "villager": "村民", "seer": "预言家",
    "witch": "女巫", "hunter": "猎人", "guard": "守卫",
}


def _role_cn(role: Any) -> str:
    key = getattr(role, "value", role)
    return _ROLE_CN.get(key, str(key))


def _safe_json(text: Optional[str]) -> dict:
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ================================================================
# 曲线组装（GET 用，不调 LLM）
# ================================================================

def _player_dicts(game: Game) -> list[dict]:
    """ORM 玩家 → 胜率引擎所需的纯 dict 结构。"""
    return [
        {
            "seat_number": p.seat_number,
            "role": getattr(p.role, "value", p.role),
            "is_alive": p.is_alive,
            "death_round": p.death_round,
            "death_phase": p.death_phase,
        }
        for p in sorted(game.players, key=lambda x: x.seat_number)
    ]


async def _load_game(db: AsyncSession, game_id: str) -> Optional[Game]:
    result = await db.execute(
        select(Game).options(selectinload(Game.players)).where(Game.id == game_id)
    )
    return result.scalar_one_or_none()


async def _load_cached_insight(db: AsyncSession, game_id: str) -> tuple[Optional[dict], bool, bool, Optional[str]]:
    """读取已缓存点评。返回 (insight, generated, is_fallback, model_name)。"""
    result = await db.execute(select(GameReview).where(GameReview.game_id == game_id))
    rec = result.scalar_one_or_none()
    if not rec:
        return None, False, False, None
    insight = None
    if rec.review_json:
        try:
            parsed = json.loads(rec.review_json)
            if isinstance(parsed, dict) and parsed:
                insight = parsed
        except (json.JSONDecodeError, TypeError):
            insight = None
    return insight, True, bool(rec.is_fallback), rec.model_name


async def build_review_data(db: AsyncSession, game_id: str) -> Optional[dict]:
    """组装胜率曲线 + 转折点 + 已缓存点评（不调 LLM）。

    返回 ReviewData 的 dict；对局不存在返回 None；未结束抛 ValueError（router 转 400）。
    """
    game = await _load_game(db, game_id)
    if not game:
        return None
    if game.status != "finished":
        raise ValueError("unfinished")

    curve = build_win_curve(_player_dicts(game), game.total_rounds or 0, game.winner)
    turns = find_turning_points(curve, top_n=3)
    insight, generated, is_fallback, model_name = await _load_cached_insight(db, game_id)

    return {
        "game_id": game_id,
        "win_curve": curve,
        "turning_points": turns,
        "insight": insight,
        "generated": generated,
        "is_fallback": is_fallback,
        "model_name": model_name,
    }


# ================================================================
# AI 点评生成（POST 用，调 LLM + 写缓存）
# ================================================================

def _brief_event(event_type: str, data: dict) -> str:
    """关键事件的简要描述（上帝视角，可揭示查验结果）。"""
    target = data.get("target")
    mapping = {
        "night_kill": f"狼刀 {target}号" if target else "狼人击杀",
        "night_verify": f"查验 {target}号 → {data.get('result', '?')}" if target else "预言家查验",
        "night_save": "女巫使用解药",
        "night_poison": f"女巫毒 {target}号" if target else "女巫使用毒药",
        "night_guard": f"守卫守护 {target}号" if target else "守卫守护",
        "hunter_shot": f"猎人开枪带走 {target}号" if target else "猎人开枪",
        "hunter_revenge": "猎人放弃开枪",
        "death_announce": f"公布死亡 {data.get('deaths')}",
        "eliminate": f"淘汰 {target}号" if target else "淘汰",
        "vote_result": f"票数 {data.get('tally')}",
        "pk_announce": f"平票 PK {data.get('tied_seats')}",
        "game_over": f"{data.get('winner')} 阵营胜利",
    }
    return mapping.get(event_type, "")


async def _build_game_summary(db: AsyncSession, game: Game) -> str:
    """组装上帝视角对局事实摘要（身份 / 关键事件 / 投票流向 / 发言）。"""
    winner_cn = "好人" if game.winner == "villager" else ("狼人" if game.winner == "werewolf" else "?")
    lines: list[str] = [
        f"模式: {game.mode}｜人数: {game.player_count}｜总轮数: {game.total_rounds}"
        f"｜胜方: {winner_cn}｜结束原因: {game.end_reason}"
    ]

    # ─── 玩家身份（上帝视角） ───
    lines.append("\n【玩家身份】")
    for p in sorted(game.players, key=lambda x: x.seat_number):
        if p.is_alive:
            status = "存活"
        else:
            phase_cn = "夜" if p.death_phase == "night" else "白天"
            status = f"第{p.death_round}轮{phase_cn}出局"
        lines.append(f"{p.seat_number}号 {p.player_name} - {_role_cn(p.role)}（{status}）")

    # ─── 关键事件时间线（排除狼队协商与噪音事件） ───
    ev_result = await db.execute(
        select(GameEvent)
        .where(
            GameEvent.game_id == game.id,
            GameEvent.event_type.notin_(_EXCLUDED_EVENT_TYPES | _SKIP_EVENT_TYPES),
        )
        .order_by(GameEvent.created_at, GameEvent.id)
    )
    events = ev_result.scalars().all()
    if events:
        lines.append("\n【关键事件】")
        for e in events:
            data = _safe_json(e.event_data)
            brief = _brief_event(e.event_type, data)
            actor = f"（{e.seat_number}号）" if e.seat_number is not None else ""
            lines.append(f"第{e.round_number}轮[{e.phase}] {e.event_type}{actor} {brief}".rstrip())

    # ─── 投票流向（按轮次 + 是否 PK 聚合） ───
    vote_result = await db.execute(
        select(Vote).where(Vote.game_id == game.id).order_by(Vote.round_number, Vote.is_pk, Vote.voter_seat)
    )
    votes = vote_result.scalars().all()
    if votes:
        lines.append("\n【投票流向】")
        for (rnd, is_pk), grp in groupby(votes, key=lambda v: (v.round_number, v.is_pk)):
            flow = "，".join(
                f"{v.voter_seat}号→{v.target_seat if v.target_seat is not None else '弃'}"
                for v in grp
            )
            lines.append(f"第{rnd}轮[{'PK' if is_pk else '投票'}]: {flow}")

    # ─── 发言记录（含遗言 / PK，单条截断） ───
    msg_result = await db.execute(
        select(ChatMessage).where(ChatMessage.game_id == game.id).order_by(ChatMessage.created_at, ChatMessage.id)
    )
    msgs = msg_result.scalars().all()
    if msgs:
        lines.append("\n【发言记录】")
        for m in msgs:
            tag = "遗言" if m.is_last_words else ("PK" if m.is_pk else "发言")
            content = (m.content or "").strip().replace("\n", " ")[:_SPEECH_SNIPPET_LEN]
            lines.append(f"第{m.round_number}轮 {m.seat_number}号[{tag}]: {content}")

    return "\n".join(lines)


def _format_turning_points(turns: list[dict]) -> str:
    """把规则计算的转折点转成给 LLM 的文字描述。"""
    lines: list[str] = []
    for t in turns:
        direction = "上升" if t.get("delta", 0) > 0 else "下降"
        pct = abs(round(t.get("delta", 0) * 100, 1))
        cur = round(t.get("good_win_prob", 0) * 100, 1)
        lines.append(
            f"第{t.get('round')}轮 {t.get('checkpoint')}（{t.get('event_label')}）："
            f"好人胜率{direction} {pct} 个百分点 → {cur}%"
        )
    return "\n".join(lines)


def _normalize_insight(parsed: dict) -> dict:
    """把 LLM 输出规整为 ReviewInsight 结构（防御性清洗，缺字段给默认值）。"""
    def _to_int(v: Any) -> Optional[int]:
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    mvp = None
    mvp_raw = parsed.get("mvp")
    if isinstance(mvp_raw, dict) and _to_int(mvp_raw.get("seat")) is not None:
        mvp = {
            "seat": _to_int(mvp_raw.get("seat")),
            "role": str(mvp_raw.get("role", "")),
            "reason": str(mvp_raw.get("reason", "")),
        }

    def _moments(lst: Any) -> list[dict]:
        out = []
        for it in (lst if isinstance(lst, list) else [])[:6]:
            if isinstance(it, dict):
                out.append({
                    "round": _to_int(it.get("round")),
                    "event": str(it.get("event", "")),
                    "impact": str(it.get("impact", "")),
                    "comment": str(it.get("comment", "")),
                })
        return out

    def _plays(lst: Any) -> list[dict]:
        out = []
        for it in (lst if isinstance(lst, list) else [])[:6]:
            if isinstance(it, dict):
                out.append({
                    "seat": _to_int(it.get("seat")),
                    "round": _to_int(it.get("round")),
                    "action": str(it.get("action", "")),
                    "comment": str(it.get("comment", "")),
                })
        return out

    camp_raw = parsed.get("camp_analysis")
    camp = {str(k): str(v) for k, v in camp_raw.items()} if isinstance(camp_raw, dict) else {}

    return {
        "summary": str(parsed.get("summary", "")),
        "mvp": mvp,
        "key_moments": _moments(parsed.get("key_moments")),
        "best_plays": _plays(parsed.get("best_plays")),
        "worst_plays": _plays(parsed.get("worst_plays")),
        "camp_analysis": camp,
    }


def _is_empty_insight(insight: dict) -> bool:
    """判断规整后的点评是否为空壳。

    未配置真实 LLM 时 create_llm 会降级为 MockWerewolfLLM，其输出为合法但无关的
    决策 JSON（decision/reasoning），规整后各字段均为空——应视为无效并降级。
    """
    return not (
        insight.get("summary")
        or insight.get("mvp")
        or insight.get("key_moments")
        or insight.get("best_plays")
        or insight.get("worst_plays")
        or insight.get("camp_analysis")
    )


def _resp_model_name(resp: Any, settings: Any) -> Optional[str]:
    meta = getattr(resp, "response_metadata", None) or {}
    return meta.get("model_name") or settings.llm_action_speech_model or settings.llm_model_name


async def _save_review(
    db: AsyncSession, game_id: str, insight: Optional[dict],
    model_name: Optional[str], is_fallback: bool,
    prompt_tokens: Optional[int], completion_tokens: Optional[int],
) -> None:
    """写入/更新复盘缓存（game_id 唯一）。降级时 insight=None → review_json 存空串。"""
    payload = json.dumps(insight, ensure_ascii=False) if insight is not None else ""
    result = await db.execute(select(GameReview).where(GameReview.game_id == game_id))
    rec = result.scalar_one_or_none()
    if rec:
        rec.review_json = payload
        rec.model_name = model_name
        rec.is_fallback = is_fallback
        rec.prompt_tokens = prompt_tokens
        rec.completion_tokens = completion_tokens
        rec.generated_at = datetime.now()
    else:
        db.add(GameReview(
            game_id=game_id,
            review_json=payload,
            model_name=model_name,
            is_fallback=is_fallback,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ))
    await db.commit()


async def generate_review(db: AsyncSession, game_id: str) -> Optional[dict]:
    """生成（或重新生成）AI 复盘点评并缓存，返回最新 ReviewData dict。

    review_llm_enabled=False 或 LLM 异常/解析失败 → 降级（insight=None, is_fallback=True），
    胜率曲线照常返回。对局不存在返回 None；未结束抛 ValueError。
    """
    data = await build_review_data(db, game_id)
    if data is None:
        return None

    settings = get_settings()

    # ─── 开关关闭：直接降级，不调 LLM ───
    if not settings.review_llm_enabled:
        await _save_review(db, game_id, None, None, True, None, None)
        data.update({"insight": None, "generated": True, "is_fallback": True, "model_name": None})
        return data

    # ─── 组装上帝视角素材 ───
    game = await _load_game(db, game_id)
    summary = await _build_game_summary(db, game)
    turns_text = _format_turning_points(data["turning_points"])

    try:
        messages = build_review_prompt(summary, turns_text)
        llm = create_llm(action_type="review", game_id=game_id)
        resp = await llm.ainvoke(messages)
        raw = resp.content if hasattr(resp, "content") else str(resp)
        parsed = _extract_json(raw.strip() if isinstance(raw, str) else "")
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("复盘 JSON 解析失败")
        insight = _normalize_insight(parsed)
        if _is_empty_insight(insight):
            raise ValueError("复盘点评为空壳（模型未产出有效结构，如未配置真实 LLM）")
        usage = getattr(resp, "usage_metadata", None) or {}
        model_name = _resp_model_name(resp, settings)
        await _save_review(
            db, game_id, insight, model_name, False,
            usage.get("input_tokens"), usage.get("output_tokens"),
        )
        data.update({"insight": insight, "generated": True, "is_fallback": False, "model_name": model_name})
    except Exception as e:
        logger.warning(f"[Review] 对局 {game_id[:8]} 复盘点评生成失败，降级: {e}")
        await _save_review(db, game_id, None, None, True, None, None)
        data.update({"insight": None, "generated": True, "is_fallback": True, "model_name": None})

    return data
