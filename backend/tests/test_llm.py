"""AI 狼人杀 — LLM 封装单元测试

测试技术方案 §5.4：
- create_llm(): 从配置创建 ChatModel 实例
- MockWerewolfLLM: 测试用 Mock LLM（返回合法结构化决策）

测试策略：
- create_llm 返回的是 BaseChatModel 实例（类型检查）
- Mock LLM 的 invoke 返回预定义文本（行为检查）
- 不实际调用 LLM API（全部 Mock）
"""

from unittest.mock import patch

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.llm import MockWerewolfLLM, create_llm

# ================================================================
# create_llm — 工厂函数测试
# ================================================================

class TestCreateLLM:
    """测试 LLM 工厂函数"""

    def test_create_llm_returns_chat_model(self):
        """create_llm 应返回 BaseChatModel 子类实例"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://test.example.com/v1",
            "LLM_MODEL_NAME": "test-model",
        }, clear=False):
            llm = create_llm()
            assert isinstance(llm, BaseChatModel)

    def test_create_llm_with_custom_model(self):
        """create_llm 应支持自定义模型名"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://test.example.com/v1",
        }, clear=False):
            llm = create_llm(model_name="custom-model")
            assert isinstance(llm, BaseChatModel)

    def test_create_llm_with_custom_temperature(self):
        """create_llm 应支持自定义温度"""
        with patch.dict("os.environ", {
            "LLM_API_KEY": "test-key",
            "LLM_BASE_URL": "https://test.example.com/v1",
            "LLM_MODEL_NAME": "test-model",
        }, clear=False):
            llm = create_llm(temperature=0.1)
            assert isinstance(llm, BaseChatModel)


# ================================================================
# MockWerewolfLLM — Mock LLM 测试
# ================================================================

class TestMockWerewolfLLM:
    """测试 Mock LLM（用于测试环境，不调用真实 API）"""

    def test_mock_llm_is_chat_model(self):
        """MockWerewolfLLM 应是 BaseChatModel 子类"""
        llm = MockWerewolfLLM()
        assert isinstance(llm, BaseChatModel)

    def test_mock_llm_invoke_returns_string(self):
        """MockWerewolfLLM.invoke() 应返回字符串"""
        llm = MockWerewolfLLM()
        result = llm.invoke([HumanMessage(content="选择击杀目标")])
        assert isinstance(result, AIMessage)
        assert isinstance(result.content, str)

    def test_mock_llm_returns_valid_kill_json(self):
        """MockWerewolfLLM 对 kill 类型应返回合法 JSON"""
        llm = MockWerewolfLLM(decision_type="kill")
        result = llm.invoke([HumanMessage(content="选择击杀目标")])

        import json
        data = json.loads(result.content)
        assert "decision" in data
        assert "reasoning" in data
        # decision 应是整数座位号
        assert isinstance(data["decision"], int)

    def test_mock_llm_returns_valid_speech(self):
        """MockWerewolfLLM 对 speech 类型应返回文本"""
        llm = MockWerewolfLLM(decision_type="speech")
        result = llm.invoke([HumanMessage(content="请发言")])

        import json
        data = json.loads(result.content)
        assert isinstance(data["decision"], str)
        assert len(data["decision"]) > 0

    def test_mock_llm_returns_valid_vote(self):
        """MockWerewolfLLM 对 vote 类型应返回座位号或 null"""
        llm = MockWerewolfLLM(decision_type="vote")
        result = llm.invoke([HumanMessage(content="请投票")])

        import json
        data = json.loads(result.content)
        # decision 应是 int 或 None
        assert data["decision"] is None or isinstance(data["decision"], int)

    def test_mock_llm_returns_valid_verify(self):
        """MockWerewolfLLM 对 verify 类型应返回座位号"""
        llm = MockWerewolfLLM(decision_type="verify")
        result = llm.invoke([HumanMessage(content="选择查验目标")])

        import json
        data = json.loads(result.content)
        assert isinstance(data["decision"], int)

    def test_mock_llm_returns_valid_save(self):
        """MockWerewolfLLM 对 save 类型应返回 bool"""
        llm = MockWerewolfLLM(decision_type="save")
        result = llm.invoke([HumanMessage(content="是否使用解药")])

        import json
        data = json.loads(result.content)
        assert isinstance(data["decision"], bool)
