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


class FixedDecisionLLM(MockWerewolfLLM):
    fixed_content: str = '{"decision": 99, "reasoning": "越界目标"}'

    def _generate(self, messages, stop=None, **kwargs):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        msg = AIMessage(content=self.fixed_content)
        return ChatResult(generations=[ChatGeneration(message=msg, text=self.fixed_content)])


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

    def test_run_agent_guard_fallback_consumes_allowed_target_seats_and_revalidates(self):
        """guard 主链非法输出后，fallback 只能从服务端合法集合中选择并通过同一校验。"""
        state = _make_test_state(
            seat_number=6,
            role="guard",
            action_type="guard",
            llm=FixedDecisionLLM(),
        )
        state["game_context"]["players"][5]["role"] = "guard"
        state["game_context"]["allowed_target_seats"] = [3, 4]

        result = run_agent(state)

        assert result["is_fallback"] is True
        assert result["decision"] in [3, 4]

    def test_run_agent_hunter_can_fallback_to_skip_when_no_allowed_targets(self):
        """hunter_shoot 主链支持可跳过契约，避免越界目标或随机主路径。"""
        state = _make_test_state(
            seat_number=5,
            role="hunter",
            action_type="hunter_shoot",
            llm=FixedDecisionLLM(),
        )
        state["game_context"]["players"][4]["role"] = "hunter"
        state["game_context"]["pending_hunter_shot"] = {"seat_number": 5, "trigger": "voted_out"}
        state["game_context"]["allowed_target_seats"] = []
        state["game_context"]["can_skip"] = True

        result = run_agent(state)

        assert result["is_fallback"] is True
        assert result["decision"] is None

    def test_run_agent_persists_agent_log_with_filtered_context_and_fallback_info(self, tmp_path, monkeypatch):
        """run_agent 完成后应持久化 AgentLog，包含过滤上下文、原始输出、解析值和 fallback 标记。"""
        from sqlalchemy import create_engine, select, text
        from sqlalchemy.orm import Session
        from app.db.session import Base
        from app.models.game import AgentLog, Game

        # 构造临时 SQLite 数据库
        db_path = tmp_path / "agent-log-test.db"
        sync_engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(sync_engine, tables=[Game.__table__, AgentLog.__table__])
        with Session(sync_engine) as session:
            session.add(Game(id="game-log-1", mode="pure_ai", status="playing", total_rounds=0))
            session.commit()

        # 让 run_agent 内部的 sync 引擎指向临时库
        import app.graphs.agent_graph as ag_mod
        original_get_sync_engine = getattr(ag_mod, "_get_sync_engine", None)
        monkeypatch.setattr(ag_mod, "_get_sync_engine", lambda: sync_engine)

        state = _make_test_state()
        state["game_context"]["game_id"] = "game-log-1"
        result = run_agent(state)

        with Session(sync_engine) as session:
            logs = session.execute(select(AgentLog).where(AgentLog.game_id == "game-log-1")).scalars().all()

        assert len(logs) == 1
        log = logs[0]
        assert log.seat_number == 1
        assert log.action_type == "kill"
        assert log.round_number == 1
        assert log.context_json is not None  # 过滤后上下文已持久化
        assert log.parsed_decision is not None  # 解析结果已持久化
        assert log.is_fallback == result["is_fallback"]
        assert log.latency_ms is not None

        sync_engine.dispose()
