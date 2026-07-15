"""AI 狼人杀 — 决策解析器单元测试

测试技术方案 §5.5：
- parse_decision(): 从 LLM 输出中解析结构化决策
- validate_decision(): 校验决策合法性

解析流程：LLM 输出 → 提取 JSON → 解析为 dict → 合法性校验
"""

import pytest
from app.agent.decision_parser import parse_decision, validate_decision


# ================================================================
# parse_decision — 从 LLM 文本输出中提取决策
# ================================================================

class TestParseDecision:
    """测试 LLM 输出解析"""

    def test_parse_valid_json(self):
        """应能解析标准 JSON 格式的决策"""
        llm_output = '{"decision": 3, "reasoning": "我选择击杀3号"}'
        result = parse_decision(llm_output)
        assert result["decision"] == 3
        assert "reasoning" in result

    def test_parse_json_in_markdown_block(self):
        """应能从 Markdown 代码块中提取 JSON"""
        llm_output = """好的，我的决策如下：
```json
{"decision": 2, "reasoning": "选择击杀2号玩家"}
```
"""
        result = parse_decision(llm_output)
        assert result["decision"] == 2

    def test_parse_json_in_text(self):
        """应能从混合文本中提取 JSON"""
        llm_output = '经过分析，我决定 {"decision": 5, "reasoning": "5号最可疑"}'
        result = parse_decision(llm_output)
        assert result["decision"] == 5

    def test_parse_speech_decision(self):
        """应能解析发言类型的决策（decision 是字符串）"""
        llm_output = '{"decision": "大家好，我觉得1号很可疑", "reasoning": "根据发言分析"}'
        result = parse_decision(llm_output)
        assert isinstance(result["decision"], str)
        assert len(result["decision"]) > 0

    def test_parse_null_decision(self):
        """应能解析 null/None 决策（弃票/不用药）"""
        llm_output = '{"decision": null, "reasoning": "我选择弃票"}'
        result = parse_decision(llm_output)
        assert result["decision"] is None

    def test_parse_bool_decision(self):
        """应能解析布尔决策（解药）"""
        llm_output = '{"decision": true, "reasoning": "使用解药"}'
        result = parse_decision(llm_output)
        assert result["decision"] is True

    def test_parse_invalid_json_returns_none(self):
        """无法解析时应返回 None（由调用方决定降级策略）"""
        result = parse_decision("这不是 JSON，只是普通文本")
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """空字符串应返回 None"""
        assert parse_decision("") is None
        assert parse_decision("   ") is None


# ================================================================
# validate_decision — 校验决策合法性
# ================================================================

class TestValidateDecision:
    """测试决策合法性校验"""

    def test_valid_kill_target(self):
        """合法的击杀目标应通过校验"""
        valid, _ = validate_decision(
            decision=3,
            action_type="kill",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=1,
            werewolf_seats=[1, 2],
        )
        assert valid is True

    def test_kill_werewolf_companion_invalid(self):
        """击杀狼人同伴应不合法"""
        valid, reason = validate_decision(
            decision=2,
            action_type="kill",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=1,
            werewolf_seats=[1, 2],
        )
        assert valid is False
        assert "狼人" in reason or "同伴" in reason

    def test_kill_self_invalid(self):
        """击杀自己应不合法"""
        valid, reason = validate_decision(
            decision=1,
            action_type="kill",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=1,
            werewolf_seats=[1, 2],
        )
        assert valid is False

    def test_kill_dead_player_invalid(self):
        """击杀已死亡玩家应不合法"""
        valid, _ = validate_decision(
            decision=4,
            action_type="kill",
            alive_seats=[1, 2, 3, 5, 6],  # 4号已死
            own_seat=1,
            werewolf_seats=[1, 2],
        )
        assert valid is False

    def test_valid_vote(self):
        """合法的投票应通过校验"""
        valid, _ = validate_decision(
            decision=3,
            action_type="vote",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=1,
        )
        assert valid is True

    def test_null_vote_is_valid(self):
        """弃票（null）应合法"""
        valid, _ = validate_decision(
            decision=None,
            action_type="vote",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=1,
        )
        assert valid is True

    def test_valid_speech(self):
        """非空发言文本应合法"""
        valid, _ = validate_decision(
            decision="大家好，我觉得1号很可疑",
            action_type="speech",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=3,
        )
        assert valid is True

    def test_empty_speech_invalid(self):
        """空发言应不合法"""
        valid, _ = validate_decision(
            decision="",
            action_type="speech",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=3,
        )
        assert valid is False

    def test_bool_save_valid(self):
        """布尔值解药决策应合法"""
        valid, _ = validate_decision(
            decision=True,
            action_type="save",
            alive_seats=[1, 2, 3, 4, 5, 6],
            own_seat=6,
        )
        assert valid is True
