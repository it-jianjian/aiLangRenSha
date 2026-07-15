"""AI 狼人杀 — Prompt 模板单元测试

测试技术方案 §5.3：
- get_role_strategy(): 各角色的策略文本
- get_decision_instruction(): 各决策类型的指令文本
- build_agent_prompt(): 完整 Prompt 构建

Prompt 四部分：System Prompt + Role Strategy + Game Context + Decision Instruction
"""

import pytest
from langchain_core.messages import SystemMessage, HumanMessage

from app.agent.prompts import (
    get_role_strategy,
    get_decision_instruction,
    build_agent_prompt,
)


class TestRoleStrategy:
    """测试各角色的策略文本"""

    @pytest.mark.parametrize("role", ["werewolf", "villager", "seer", "witch"])
    def test_all_roles_have_strategy(self, role):
        """每个角色都应有非空的策略文本"""
        strategy = get_role_strategy(role)
        assert isinstance(strategy, str)
        assert len(strategy) > 10  # 不能太短

    def test_werewolf_strategy_mentions_disguise(self):
        """狼人策略应提到伪装/隐藏身份"""
        strategy = get_role_strategy("werewolf")
        assert "伪装" in strategy or "隐藏" in strategy or "身份" in strategy

    def test_seer_strategy_mentions_verify(self):
        """预言家策略应提到查验"""
        strategy = get_role_strategy("seer")
        assert "查验" in strategy or "身份" in strategy

    def test_witch_strategy_mentions_potion(self):
        """女巫策略应提到解药/毒药"""
        strategy = get_role_strategy("witch")
        assert "解药" in strategy or "毒药" in strategy or "药" in strategy

    def test_villager_strategy_mentions_reasoning(self):
        """村民策略应提到推理/分析"""
        strategy = get_role_strategy("villager")
        assert "推理" in strategy or "分析" in strategy or "发言" in strategy


class TestDecisionInstruction:
    """测试各决策类型的指令文本"""

    @pytest.mark.parametrize("action_type", [
        "kill", "verify", "save", "poison", "speech", "vote", "last_words",
    ])
    def test_all_action_types_have_instruction(self, action_type):
        """每种决策类型都应有非空的指令文本"""
        instruction = get_decision_instruction(action_type)
        assert isinstance(instruction, str)
        assert len(instruction) > 5

    def test_kill_instruction_mentions_json_format(self):
        """击杀指令应包含 JSON 格式要求"""
        instruction = get_decision_instruction("kill")
        assert "decision" in instruction or "JSON" in instruction or "json" in instruction

    def test_speech_instruction_mentions_text(self):
        """发言指令应提到文本/发言"""
        instruction = get_decision_instruction("speech")
        assert "发言" in instruction or "文本" in instruction or "文字" in instruction


class TestBuildAgentPrompt:
    """测试完整 Prompt 构建"""

    def test_build_prompt_returns_messages(self):
        """build_agent_prompt 应返回消息列表"""
        messages = build_agent_prompt(
            role="werewolf",
            seat_number=1,
            action_type="kill",
            game_context={"current_round": 1, "alive_seats": [1, 2, 3, 4, 5, 6]},
        )
        assert isinstance(messages, list)
        assert len(messages) >= 2  # 至少 System + Human

    def test_build_prompt_contains_system_message(self):
        """构建的 Prompt 应包含 SystemMessage"""
        messages = build_agent_prompt(
            role="seer", seat_number=5, action_type="verify",
            game_context={"current_round": 2},
        )
        assert any(isinstance(m, SystemMessage) for m in messages)

    def test_build_prompt_contains_human_message(self):
        """构建的 Prompt 应包含 HumanMessage（含策略+上下文+指令）"""
        messages = build_agent_prompt(
            role="witch", seat_number=6, action_type="save",
            game_context={"current_round": 1},
        )
        human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
        assert len(human_msgs) >= 1
        # Human message 应包含角色策略相关内容
        content = human_msgs[0].content
        assert "药" in content or "解药" in content
