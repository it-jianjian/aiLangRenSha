"""复盘引擎单测：胜率曲线组装 / AI 点评解析入库 / 全链路 fallback / 协商隔离红线。"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import Base
from app.models.game import ChatMessage, Game, GameEvent, GamePlayer, GameReview, Vote
from app.services import game_review
from app.services.game_review import (
    _brief_event,
    _format_turning_points,
    _normalize_insight,
    build_review_data,
    generate_review,
)

SECRET_REASON = "SECRET_WOLF_REASON_XYZ"

_VALID_REVIEW = json.dumps({
    "summary": "好人靠预言家信息稳扎稳打取胜",
    "mvp": {"seat": 5, "role": "seer", "reason": "查验准确带动票型"},
    "key_moments": [{"round": 2, "event": "投出1号狼", "impact": "好人夺回节奏", "comment": "关键一票"}],
    "best_plays": [{"seat": 5, "round": 2, "action": "跳预言家报查杀", "comment": "信息及时"}],
    "worst_plays": [{"seat": 3, "round": 1, "action": "无依据乱踩", "comment": "暴露视角"}],
    "camp_analysis": {"good": "神职配合好", "wolf": "狼刀分散"},
}, ensure_ascii=False)


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 100, "output_tokens": 50}
        self.response_metadata = {"model_name": "test-review-model"}


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        if isinstance(self._content, Exception):
            raise self._content
        return _FakeResp(self._content)


async def _make_session(tmp_path, name="review.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=[
            Game.__table__, GamePlayer.__table__, GameEvent.__table__,
            Vote.__table__, ChatMessage.__table__, GameReview.__table__,
        ]))
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session, game_id="g-review", status="finished", winner="villager"):
    """造一局已结束对局：2狼全灭好人胜，含协商事件（哨兵串）用于隔离断言。"""
    session.add(Game(id=game_id, mode="pure_ai", status=status, winner=winner,
                     end_reason="all_werewolf_dead", total_rounds=3, player_count=6))
    players = [
        ("p1", 1, "werewolf", "狼1", False, 2, "day"),
        ("p2", 2, "werewolf", "狼2", False, 3, "day"),
        ("p3", 3, "villager", "民1", False, 1, "night"),
        ("p4", 4, "villager", "民2", True, None, None),
        ("p5", 5, "seer", "预言家", True, None, None),
        ("p6", 6, "witch", "女巫", False, 2, "night"),
    ]
    for pid, seat, role, name, alive, dr, dp in players:
        session.add(GamePlayer(id=pid, game_id=game_id, seat_number=seat, player_type="ai",
                               role=role, player_name=name, is_alive=alive, death_round=dr, death_phase=dp))
    session.add(GameEvent(id="e1", game_id=game_id, round_number=1, phase="night",
                          event_type="night_kill", event_data=json.dumps({"target": 3})))
    session.add(GameEvent(id="e2", game_id=game_id, round_number=1, phase="night",
                          event_type="werewolf_negotiation",
                          event_data=json.dumps({"proposals": {"1": {"target": 3, "reason": SECRET_REASON}}}, ensure_ascii=False)))
    session.add(GameEvent(id="e3", game_id=game_id, round_number=2, phase="day",
                          event_type="eliminate", seat_number=1, event_data=json.dumps({"target": 1})))
    session.add(Vote(id="v1", game_id=game_id, round_number=2, voter_seat=4, target_seat=1, is_pk=False))
    session.add(Vote(id="v2", game_id=game_id, round_number=2, voter_seat=5, target_seat=1, is_pk=False))
    session.add(ChatMessage(id="m1", game_id=game_id, round_number=1, seat_number=5,
                            content="我是预言家，昨晚查了3号是好人", is_pk=False, is_last_words=False))
    await session.commit()


def _install_llm(monkeypatch, content):
    monkeypatch.setattr(game_review, "create_llm", lambda **kw: _FakeLLM(content))


# ─── 纯函数 ───────────────────────────────────────────────

def test_normalize_insight_cleans_dirty_output():
    dirty = {
        "summary": "总评", "mvp": {"seat": "5", "role": "seer"},
        "key_moments": "不是列表", "best_plays": [{"seat": "x", "action": "A"}],
        "worst_plays": None, "camp_analysis": "不是字典",
    }
    out = _normalize_insight(dirty)
    assert out["summary"] == "总评"
    assert out["mvp"]["seat"] == 5                       # 字符串座位号转 int
    assert out["key_moments"] == []                       # 非列表清洗为空
    assert out["best_plays"][0]["seat"] is None           # 非法 int 归 None
    assert out["worst_plays"] == []
    assert out["camp_analysis"] == {}


def test_format_turning_points_direction():
    turns = [{"round": 2, "checkpoint": "after_day", "event_label": "1号被放逐",
              "delta": 0.15, "good_win_prob": 0.6}]
    text = _format_turning_points(turns)
    assert "上升" in text and "15" in text


def test_brief_event_reveals_verify_result_god_view():
    assert "查杀" in _brief_event("night_verify", {"target": 3, "result": "查杀"}) or \
           "3号" in _brief_event("night_verify", {"target": 3, "result": "werewolf"})


# ─── 曲线组装（不调 LLM） ─────────────────────────────────

@pytest.mark.asyncio
async def test_build_review_data_missing_returns_none(tmp_path):
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        assert await build_review_data(session, "not-exist") is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_build_review_data_unfinished_raises(tmp_path):
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session, status="playing", winner=None)
        with pytest.raises(ValueError):
            await build_review_data(session, "g-review")
    await engine.dispose()


@pytest.mark.asyncio
async def test_build_review_data_curve_without_insight(tmp_path):
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        data = await build_review_data(session, "g-review")
    assert data["insight"] is None and data["generated"] is False
    curve = data["win_curve"]
    assert curve[0]["checkpoint"] == "start" and curve[0]["alive_wolves"] == 2
    assert curve[-1]["checkpoint"] == "final" and curve[-1]["good_win_prob"] == 1.0
    assert len(data["turning_points"]) > 0
    await engine.dispose()


# ─── AI 点评生成 + 缓存 ───────────────────────────────────

@pytest.mark.asyncio
async def test_generate_review_parses_and_caches(tmp_path, monkeypatch):
    _install_llm(monkeypatch, _VALID_REVIEW)
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        data = await generate_review(session, "g-review")
        assert data["is_fallback"] is False and data["generated"] is True
        assert data["insight"]["mvp"]["seat"] == 5
        assert data["model_name"] == "test-review-model"
        # 缓存已落库
        rec = (await session.execute(select(GameReview).where(
            GameReview.game_id == "g-review"))).scalar_one()
        assert rec.is_fallback is False and rec.prompt_tokens == 100
        # 二次读取命中缓存
        again = await build_review_data(session, "g-review")
        assert again["insight"]["summary"] == "好人靠预言家信息稳扎稳打取胜"
    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_review_llm_error_falls_back(tmp_path, monkeypatch):
    _install_llm(monkeypatch, RuntimeError("LLM 超时"))
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        data = await generate_review(session, "g-review")
    assert data["insight"] is None and data["is_fallback"] is True
    assert len(data["win_curve"]) > 0, "降级时胜率曲线仍须返回"
    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_review_invalid_json_falls_back(tmp_path, monkeypatch):
    _install_llm(monkeypatch, "这不是 JSON，模型跑偏了")
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        data = await generate_review(session, "g-review")
    assert data["insight"] is None and data["is_fallback"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_review_empty_struct_falls_back(tmp_path, monkeypatch):
    """模型产出合法但无关的 JSON（如 Mock 的 decision 结构）→ 视为空壳降级。"""
    _install_llm(monkeypatch, json.dumps({"decision": 3, "reasoning": "mock kill"}))
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        data = await generate_review(session, "g-review")
    assert data["insight"] is None and data["is_fallback"] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_review_disabled_flag_skips_llm(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(game_review, "create_llm", lambda **kw: calls.append(1))
    monkeypatch.setattr(game_review, "get_settings",
                        lambda: SimpleNamespace(review_llm_enabled=False))
    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        data = await generate_review(session, "g-review")
    assert data["is_fallback"] is True and data["insight"] is None
    assert calls == [], "开关关闭时绝不调用 LLM"
    await engine.dispose()


# ─── 隔离红线 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_excludes_werewolf_negotiation(tmp_path, monkeypatch):
    """复盘素材（喂给 LLM 的 summary）绝不含狼队协商原始 reason。"""
    captured = {}

    def fake_prompt(summary, turns):
        captured["summary"] = summary
        return [SimpleNamespace(content=summary)]

    monkeypatch.setattr(game_review, "build_review_prompt", fake_prompt)
    _install_llm(monkeypatch, _VALID_REVIEW)

    engine, factory = await _make_session(tmp_path)
    async with factory() as session:
        await _seed(session)
        await generate_review(session, "g-review")

    assert SECRET_REASON not in captured["summary"], "协商 reason 泄露进复盘素材（P0）"
    # 对照：summary 确实包含真实事实（非空跑）
    assert "预言家" in captured["summary"] and "投票流向" in captured["summary"]
    await engine.dispose()
