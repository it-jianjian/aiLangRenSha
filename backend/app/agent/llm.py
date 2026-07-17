"""AI 狼人杀 — LLM 模型封装

职责：
1. create_llm() — 从配置创建 LangChain ChatModel 实例
2. MockWerewolfLLM — 测试用 Mock LLM，返回合法结构化决策

技术方案 §5.4 对应实现：
- 统一使用 LangChain 的 BaseChatModel 抽象接口
- OpenAI-compatible API 支持所有国产模型（Qwen/ChatGLM 等）
- Mock 模型用于测试和 LLM 不可用时的降级

调用链：AgentGraph(LLMCallNode) → create_llm() → ChatOpenAI → API
                                   → MockWerewolfLLM → 合法结构化决策
"""

import json
import re
import random
import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import get_settings

logger = logging.getLogger(__name__)


def create_llm(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
) -> BaseChatModel:
    """从配置创建 LangChain ChatModel 实例

    参数:
        model_name: 模型名称（默认从 config.llm_model_name 读取）
        temperature: 温度参数（默认从 config.llm_temperature 读取）

    返回:
        BaseChatModel 实例（ChatOpenAI，兼容所有 OpenAI-compatible API）

    说明:
        所有国产模型（Qwen/ChatGLM/GLM-4 等）都提供 OpenAI-compatible 端点
        统一使用 langchain_openai.ChatOpenAI 调用，通过 base_url 切换后端

        如果 API Key 未配置或为占位符，自动降级为 MockWerewolfLLM，
        使游戏可以在无 LLM 服务的情况下运行（用于测试和开发）
    """
    settings = get_settings()
    api_key = settings.llm_api_key

    # API Key 未配置或为占位符 → 使用 Mock LLM
    if not api_key or api_key in ("your-api-key-here", "your-deepseek-api-key-here", "not-set", ""):
        logger.warning("[LLM] API Key 未配置，使用 MockWerewolfLLM 降级运行")
        return MockWerewolfLLM(decision_type="kill")  # decision_type 会被 run_agent 动态设置

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name or settings.llm_model_name,
        base_url=settings.llm_base_url,
        api_key=api_key,
        temperature=temperature if temperature is not None else settings.llm_temperature,
        timeout=settings.llm_timeout,
        max_retries=1,
        max_tokens=2048,
    )


class MockWerewolfLLM(BaseChatModel):
    """Mock LLM 模型 — 用于测试和 LLM 不可用时的降级

    继承 BaseChatModel 以便与真实 LLM 接口完全一致
    不发起任何 API 调用，直接返回预定义的结构化决策

    参数:
        decision_type: 决策类型（kill/verify/save/poison/speech/vote/last_words）

    智能策略：
        Mock 会从 SystemMessage 中解析当前游戏状态（存活玩家、自己的座位号、
        狼人同伴），确保返回的决策值是合法的（不会选到已死亡玩家或自己）。
        如果解析失败（如单元测试中未提供完整 Prompt），回退到 [1,2,3,4,5,6]。
    """

    decision_type: str = "kill"
    """当前 Mock 返回的决策类型"""

    @property
    def _llm_type(self) -> str:
        return "mock-werewolf"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """生成 Mock 响应（LangChain 内部调用）

        从 messages 中解析游戏状态，生成合法的结构化决策
        """
        # ─── 从 SystemMessage 中解析游戏状态 ───
        alive_seats, own_seat, werewolf_companions = self._parse_game_state(messages)

        decision_data = self._mock_decision(alive_seats, own_seat, werewolf_companions)
        content = json.dumps(decision_data, ensure_ascii=False)
        message = AIMessage(content=content)

        return ChatResult(
            generations=[ChatGeneration(message=message, text=content)],
            llm_output={"mock": True},
        )

    def _parse_game_state(
        self,
        messages: list[BaseMessage],
    ) -> tuple[list[int], Optional[int], list[int]]:
        """从 Prompt 消息中解析游戏状态

        build_agent_prompt() 会在 SystemMessage 中写入：
        - "你的座位号是 X 号"
        - "存活玩家座位号: [X, Y, Z]"
        - "你的狼人同伴: [X]号"

        返回:
            (alive_seats, own_seat, werewolf_companions)
        """
        alive_seats = [1, 2, 3, 4, 5, 6]  # 默认全存活
        own_seat = None
        werewolf_companions = []

        for msg in messages:
            content = msg.content if hasattr(msg, "content") else str(msg)

            # 解析存活玩家座位号: [1, 2, 3]
            alive_match = re.search(r"存活玩家座位号:\s*\[([\d,\s]+)\]", content)
            if alive_match:
                alive_seats = [int(s.strip()) for s in alive_match.group(1).split(",") if s.strip()]

            # 解析座位号: "你的座位号是 X 号"
            seat_match = re.search(r"座位号是\s*(\d+)\s*号", content)
            if seat_match:
                own_seat = int(seat_match.group(1))

            # 解析狼人同伴: "你的狼人同伴: [X]号"
            companion_match = re.search(r"狼人同伴:\s*\[([\d,\s]+)\]", content)
            if companion_match:
                werewolf_companions = [int(s.strip()) for s in companion_match.group(1).split(",") if s.strip()]

        return alive_seats, own_seat, werewolf_companions

    def _mock_decision(
        self,
        alive_seats: list[int],
        own_seat: Optional[int],
        werewolf_companions: list[int],
    ) -> dict[str, Any]:
        """根据 decision_type + 游戏状态生成合法的 Mock 决策

        参数:
            alive_seats: 存活玩家座位号列表
            own_seat: 自己的座位号
            werewolf_companions: 狼人同伴座位号列表

        保证:
            - kill: 不会选到已死亡玩家、自己、或狼人同伴
            - verify: 不会选到已死亡玩家或自己
            - poison/vote: 不会选到已死亡玩家或自己
        """
        # 计算合法目标列表（排除自己）
        other_alive = [s for s in alive_seats if s != own_seat] if own_seat else alive_seats

        match self.decision_type:
            case "kill":
                # 排除狼人同伴
                targets = [s for s in other_alive if s not in werewolf_companions]
                target = random.choice(targets) if targets else (other_alive[0] if other_alive else 1)
                return {
                    "decision": target,
                    "reasoning": f"[Mock] 选择击杀 {target}号（存活玩家中随机）",
                }

            case "verify":
                target = random.choice(other_alive) if other_alive else 1
                return {
                    "decision": target,
                    "reasoning": f"[Mock] 选择查验 {target}号（存活玩家中随机）",
                }

            case "save":
                return {
                    "decision": random.choice([True, False]),
                    "reasoning": "[Mock] 随机决定是否使用解药",
                }

            case "poison":
                # 排除自己，可以选 null（不用毒药）
                choices = other_alive + [None] if other_alive else [None]
                target = random.choice(choices)
                return {
                    "decision": target,
                    "reasoning": f"[Mock] 毒药决策: {target}",
                }

            case "speech":
                speeches = [
                    f"我是{own_seat or '?'}号，我认为我们需要更仔细地分析局势。",
                    f"{own_seat or '?'}号发言：目前信息有限，我继续观察大家的行为。",
                    f"大家好，我是{own_seat or '?'}号，这轮我有几个观察想分享。",
                ]
                return {
                    "decision": random.choice(speeches),
                    "reasoning": "[Mock] 生成占位发言",
                }

            case "vote":
                choices = other_alive + [None] if other_alive else [None]
                target = random.choice(choices)
                return {
                    "decision": target,
                    "reasoning": f"[Mock] 投票给 {target}",
                }

            case "last_words":
                last_words = [
                    f"我是{own_seat or '?'}号，希望大家能找出真正的狼人。",
                    f"{own_seat or '?'}号遗言：注意观察投票模式。",
                    f"走了，{own_seat or '?'}号留个话——相信自己的判断。",
                ]
                return {
                    "decision": random.choice(last_words),
                    "reasoning": "[Mock] 生成占位遗言",
                }

            case _:
                return {
                    "decision": None,
                    "reasoning": f"[Mock] 未知决策类型: {self.decision_type}",
                }
