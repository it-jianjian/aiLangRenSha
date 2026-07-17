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
    _format_game_context,
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

    def test_seer_strategy_warns_against_early_reveal(self):
        """预言家策略应明确警告第一天不要跳身份"""
        strategy = get_role_strategy("seer")
        assert "第一天" in strategy or "第一晚" in strategy or "不要跳" in strategy

    def test_seer_strategy_emphasizes_survival(self):
        """预言家策略应强调存活的重要性（被击杀风险）"""
        strategy = get_role_strategy("seer")
        assert "击杀" in strategy or "存活" in strategy or "活着" in strategy

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

    def test_speech_instruction_discourages_echoing(self):
        """发言指令应要求独立思考，不简单复述他人"""
        instruction = get_decision_instruction("speech")
        assert "复述" in instruction or "独立" in instruction


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

    def test_system_message_contains_core_rules(self):
        """SystemMessage 应包含核心行为规则"""
        messages = build_agent_prompt(
            role="villager", seat_number=3, action_type="speech",
            game_context={"current_round": 1},
        )
        sys_msg = next(m for m in messages if isinstance(m, SystemMessage))
        content = sys_msg.content
        assert "阵营获胜" in content
        assert "系统事实" in content
        assert "不得虚构" in content
        assert "复述" in content
        assert "JSON" in content

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


class TestPkHistoryRendering:
    """测试平票 PK 详情在过往轮次回顾中的渲染

    修复“只记得最终投票，不记得平票” bug：
    AI 应能在历史中看到上轮出现了平票、两人 battle 及当时的投票。
    """

    def _pk_history_context(self) -> dict:
        """构造含一轮 PK 历史的上下文。"""
        return {
            "current_round": 2,
            "alive_seats": [1, 2, 3, 5, 6],
            "action_type": "vote",
            "game_history": [
                {
                    "round": 1,
                    "speeches": [{"seat": 1, "content": "我是好人"}],
                    "votes": {"1": 2, "3": 2, "5": None},  # PK 重投结果
                    "night_deaths": [4],
                    "eliminated_seat": 2,
                    "is_pk": True,
                    "pk_seats": [2, 3],
                    "pre_pk_votes": {"1": 3, "2": 1, "5": 3, "6": 2},  # 首轮平票投票
                    "pk_speeches": [
                        {"seat": 2, "content": "我不是狼"},
                        {"seat": 3, "content": "投我的是狼"},
                    ],
                }
            ],
        }

    def test_pk_history_contains_tie_announcement(self):
        """历史回顾应包含平票宣布与 PK 候选人。"""
        text = _format_game_context(self._pk_history_context())
        assert "平票" in text
        assert "2号" in text and "3号" in text
        assert "PK" in text

    def test_pk_history_contains_first_round_votes(self):
        """历史回顾应包含首轮平票投票明细。"""
        text = _format_game_context(self._pk_history_context())
        assert "首轮投票" in text
        # 首轮投票中有 5号→3号
        assert "5号→3号" in text

    def test_pk_history_contains_pk_speeches(self):
        """历史回顾应包含 PK 发言（两人 battle 内容）。"""
        text = _format_game_context(self._pk_history_context())
        assert "PK 发言" in text
        assert "我不是狼" in text
        assert "投我的是狼" in text

    def test_pk_history_labels_final_votes_as_pk_revote(self):
        """PK 轮的最终投票应标注为“PK 重投”而非“投票”。"""
        text = _format_game_context(self._pk_history_context())
        assert "PK 重投" in text
        # 不应把 PK 轮的 votes 误标为普通“投票:”
        assert text.count("投票:") == 0

    def test_current_pk_indicator_shown(self):
        """当前轮处于 PK 重投时应显示 PK 指示。"""
        ctx = {"current_round": 1, "alive_seats": [1, 2, 3],
               "action_type": "vote", "is_pk": True, "pk_seats": [2, 3]}
        text = _format_game_context(ctx)
        assert "平票 PK 重投环节" in text
        assert "2" in text and "3" in text
