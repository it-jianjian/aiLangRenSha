"""AI 狼人杀 — Agent 子图单元测试

测试技术方案 §5.1：Agent 子图端到端执行
  ContextFilterNode → PromptBuildNode → LLMCallNode → DecisionParseNode

测试策略：
- 使用 MockWerewolfLLM 避免真实 API 调用
- 验证子图输入输出格式
- 验证信息隔离在子图中生效
"""

import pytest
from app.graphs.agent_graph import run_agent, AgentState
from app.agent.llm import MockWerewolfLLM


def _make_test_state(**overrides) -> AgentState:
    """创建测试用 AgentState"""
    base = {
        "seat_number": 1,
        "role": "werewolf",
        "player_name": "AI-测试狼人",
        "action_type": "kill",
        "game_context": {
            "current_round": 1,
            "players": [
                {"seat_number": 1, "player_name": "AI-1", "player_type": "ai",
                 "role": "werewolf", "is_alive": True},
                {"seat_number": 2, "player_name": "AI-2", "player_type": "ai",
                 "role": "werewolf", "is_alive": True},
                {"seat_number": 3, "player_name": "AI-3", "player_type": "ai",
                 "role": "villager", "is_alive": True},
                {"seat_number": 4, "player_name": "AI-4", "player_type": "ai",
                 "role": "villager", "is_alive": True},
                {"seat_number": 5, "player_name": "AI-5", "player_type": "ai",
                 "role": "seer", "is_alive": True},
                {"seat_number": 6, "player_name": "AI-6", "player_type": "ai",
                 "role": "witch", "is_alive": True},
            ],
            "werewolf_seats": [1, 2],
            "seer_seat": 5,
            "witch_seat": 6,
            "alive_seats": [1, 2, 3, 4, 5, 6],
            "speeches": [],
            "votes": {},
            "night_kill_target": None,
            "night_seer_target": None,
            "night_seer_result": None,
            "witch_save_used": False,
            "witch_poison_used": False,
            "night_deaths": [],
        },
        "llm": MockWerewolfLLM(decision_type="kill"),
    }
    base.update(overrides)
    return base


class TestAgentSubgraph:
    """Agent 子图端到端测试"""

    def test_run_agent_returns_decision(self):
        """run_agent 应返回包含 decision 的字典"""
        state = _make_test_state()
        result = run_agent(state)

        assert "decision" in result
        assert "reasoning" in result

    def test_run_agent_with_mock_kill(self):
        """Mock kill 应返回整数座位号"""
        state = _make_test_state(action_type="kill")
        result = run_agent(state)

        assert isinstance(result["decision"], int)
        assert result["decision"] in range(1, 7)

    def test_run_agent_with_mock_speech(self):
        """Mock speech 应返回字符串"""
        state = _make_test_state(
            action_type="speech",
            llm=MockWerewolfLLM(decision_type="speech"),
        )
        result = run_agent(state)

        assert isinstance(result["decision"], str)
        assert len(result["decision"]) > 0

    def test_run_agent_with_mock_vote(self):
        """Mock vote 应返回 int 或 None"""
        state = _make_test_state(
            action_type="vote",
            llm=MockWerewolfLLM(decision_type="vote"),
        )
        result = run_agent(state)

        assert result["decision"] is None or isinstance(result["decision"], int)

    def test_run_agent_records_latency(self):
        """run_agent 应记录延迟"""
        state = _make_test_state()
        result = run_agent(state)

        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], int)
        assert result["latency_ms"] >= 0

    def test_run_agent_records_is_fallback(self):
        """run_agent 应标记是否降级"""
        state = _make_test_state()
        result = run_agent(state)

        assert "is_fallback" in result
        assert isinstance(result["is_fallback"], bool)

    def test_run_agent_fallback_on_parse_failure(self):
        """LLM 返回不可解析内容时应降级为随机决策"""
        from langchain_core.language_models import BaseChatModel
        from langchain_core.messages import AIMessage, BaseMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class BrokenLLM(BaseChatModel):
            @property
            def _llm_type(self):
                return "broken"

            def _generate(self, messages, stop=None, **kwargs):
                content = "这不是JSON，完全无法解析"
                msg = AIMessage(content=content)
                return ChatResult(
                    generations=[ChatGeneration(message=msg, text=content)],
                )

        state = _make_test_state(llm=BrokenLLM())
        result = run_agent(state)

        # 降级后仍应有合法决策
        assert "decision" in result
        assert result["is_fallback"] is True
