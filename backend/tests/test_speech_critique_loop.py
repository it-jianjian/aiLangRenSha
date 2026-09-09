"""需求二（发言批评-修订）验收1 · 构造性单测（FR-1）。

喂入含"狼视角泄露"的 mock 草稿 → critique 必须标 high 并触发 revise，修订稿过校验；
并覆盖 FR-3 单次修订、risk none/low 不修订、评审单解析失败视为 none（宁可漏改不可阻塞）。
"""

import asyncio
import json

from app.agent import speech_critique
from app.models.game import PlayerRole


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """按 action_type 返回脚本化内容；content 为异常实例时 ainvoke 抛出。"""

    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        if isinstance(self._content, Exception):
            raise self._content
        return _FakeResp(self._content)


def _wolf_state() -> dict:
    players = [
        {"seat_number": 1, "player_name": "狼1", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 2, "player_name": "狼2", "player_type": "ai", "role": PlayerRole.WEREWOLF, "is_alive": True},
        {"seat_number": 3, "player_name": "民1", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 4, "player_name": "民2", "player_type": "ai", "role": PlayerRole.VILLAGER, "is_alive": True},
        {"seat_number": 5, "player_name": "预言家", "player_type": "ai", "role": PlayerRole.SEER, "is_alive": True},
        {"seat_number": 6, "player_name": "女巫", "player_type": "ai", "role": PlayerRole.WITCH, "is_alive": True},
    ]
    return {
        "game_id": "g-sc", "current_round": 2, "players": players, "werewolf_seats": [1, 2],
        "speeches": [],
        "game_history": [{"round": 1, "speeches": [{"seat": 1, "content": "我上轮觉得5号很可疑"}]}],
    }


def _install_llm(monkeypatch, critique_out, revise_out, calls=None):
    """monkeypatch speech_critique.create_llm：critique 走小模型、revise 走大模型。"""
    def fake_create_llm(seat_number=None, action_type=None, **kw):
        if calls is not None:
            calls.append(action_type)
        return _FakeLLM(critique_out if action_type == "critique" else revise_out)

    monkeypatch.setattr(speech_critique, "create_llm", fake_create_llm)


# ─── 验收1：狼视角泄露 → high → revise ──────────────────────


def test_identity_leak_draft_marked_high_and_revised(monkeypatch):
    calls: list = []
    review = json.dumps({
        "risk_level": "high",
        "issues": [{"type": "identity_leak", "quote": "他不像狼", "why": "上帝视角，只有狼才敢这么断定"}],
        "fix_hint": "改为基于公开票型/发言的推测",
    }, ensure_ascii=False)
    _install_llm(monkeypatch, review, "5号上轮发言没什么信息量，我建议大家再观察他的票型。", calls)

    state = _wolf_state()
    wolf = state["players"][0]
    draft = "我觉得5号不像狼，他肯定是好人，我们别投他。"  # 狼视角泄露

    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res["revised"] is True
    assert res["is_fallback"] is False
    assert res["final_text"] != draft and "不像狼" not in res["final_text"]
    summary = json.loads(res["critique_result"])
    assert summary["risk_level"] == "high"
    assert "identity_leak" in summary["issue_types"]
    assert summary["fix_hint"] == "改为基于公开票型/发言的推测"
    # FR-1③/FR-3：恰好一次 critique + 一次 revise，修订稿不再过 critique
    assert calls.count("critique") == 1
    assert calls.count("speech") == 1


def test_medium_risk_also_triggers_revise(monkeypatch):
    calls: list = []
    review = json.dumps({
        "risk_level": "medium",
        "issues": [{"type": "self_contradiction", "quote": "保5号", "why": "上轮咬5号本轮无依据保他"}],
        "fix_hint": "补充改口依据",
    }, ensure_ascii=False)
    _install_llm(monkeypatch, review, "我上轮怀疑5号，但这轮他的发言解释了疑点，我暂时保留判断。", calls)

    state = _wolf_state()
    wolf = state["players"][0]
    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", "5号肯定是好人，我保他。"))

    assert res["revised"] is True and res["is_fallback"] is False
    assert calls.count("speech") == 1


# ─── risk none/low → 不修订 ─────────────────────────────────


def test_risk_none_passes_draft_without_revise(monkeypatch):
    calls: list = []
    _install_llm(monkeypatch, json.dumps({"risk_level": "none", "issues": [], "fix_hint": ""}),
                 "不应被使用的修订稿", calls)

    state = _wolf_state()
    wolf = state["players"][0]
    draft = "大家再观察5号的票型，我暂时没有更多信息。"
    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res["final_text"] == draft
    assert res["revised"] is False and res["is_fallback"] is False
    assert calls == ["critique"], "risk=none 不应触发 revise（不调用大模型）"


def test_risk_low_passes_draft_without_revise(monkeypatch):
    calls: list = []
    _install_llm(monkeypatch, json.dumps({"risk_level": "low", "issues": [], "fix_hint": ""}),
                 "不应被使用", calls)

    state = _wolf_state()
    wolf = state["players"][0]
    draft = "随便说点观察。"
    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res["final_text"] == draft and res["revised"] is False
    assert calls == ["critique"]


# ─── 评审单解析失败 → 视为 none（FR-1） ─────────────────────


def test_critique_parse_failure_treated_as_none(monkeypatch):
    calls: list = []
    _install_llm(monkeypatch, "这不是 JSON，模型跑偏了", "不应被使用", calls)

    state = _wolf_state()
    wolf = state["players"][0]
    draft = "随便说点什么。"
    res = asyncio.run(speech_critique.critique_and_revise(state, wolf, "speech", draft))

    assert res["final_text"] == draft and res["revised"] is False and res["is_fallback"] is False
    assert json.loads(res["critique_result"])["risk_level"] == "none"
    assert calls == ["critique"]


def test_parse_critique_variants():
    p = speech_critique.parse_critique
    assert p('{"risk_level":"high","issues":[{"type":"identity_leak"}],"fix_hint":"x"}')["risk_level"] == "high"
    assert p('```json\n{"risk_level":"medium","issues":[]}\n```')["risk_level"] == "medium"
    assert p('评审如下：{"risk_level":"high","issues":[]} 完毕')["risk_level"] == "high"
    assert p("garbage")["risk_level"] == "none"
    assert p("")["risk_level"] == "none"
    assert p(None)["risk_level"] == "none"
    assert p('{"risk_level":"bogus"}')["risk_level"] == "none"  # 非法等级归一为 none
    # 非法 issues 结构被清洗为合法条目
    cleaned = p('{"risk_level":"high","issues":["不是对象",{"type":"weak_argument"}],"fix_hint":null}')
    assert cleaned["issues"] == [{"type": "weak_argument", "quote": "", "why": ""}]
    assert cleaned["fix_hint"] == ""


# ─── 修订稿兜底 + 历史抽取（FR-3/验收5 单元） ───────────────


def test_safeguards_empty_falls_back_to_draft():
    assert speech_critique._apply_speech_safeguards("", "草稿") == ("草稿", True)
    assert speech_critique._apply_speech_safeguards("   \n ", "草稿") == ("草稿", True)


def test_safeguards_truncates_overlong():
    final, fb = speech_critique._apply_speech_safeguards("字" * 1500, "草稿")
    assert len(final) == 1000 and fb is False


def test_safeguards_keeps_valid_revision():
    assert speech_critique._apply_speech_safeguards("正常修订稿", "草稿") == ("正常修订稿", False)


def test_extract_own_history_picks_only_own_seat():
    filtered = {
        "game_history": [{"round": 1, "speeches": [{"seat": 1, "content": "我上轮说A"}, {"seat": 3, "content": "别人说B"}]}],
        "speeches": [{"seat": 1, "content": "本轮我说C"}, {"seat": 5, "content": "别人说D"}],
    }
    text = speech_critique.extract_own_history(filtered, 1)
    assert "我上轮说A" in text and "本轮我说C" in text
    assert "别人说B" not in text and "别人说D" not in text
