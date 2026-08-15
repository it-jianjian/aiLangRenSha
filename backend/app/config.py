"""AI 狼人杀 — 全局配置管理

职责：集中管理所有环境变量和配置项
机制：通过 pydantic-settings 从 .env 文件和环境变量自动加载配置
优先级：环境变量 > .env 文件 > 代码中的默认值
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass
class LlmInstance:
    """单个 LLM 实例配置"""
    model_name: str
    temperature: float = 0.7
    timeout: int = 60


class Settings(BaseSettings):
    """应用全局配置

    继承 BaseSettings 后，每个字段会自动从环境变量读取（字段名转大写匹配）
    例如：database_url 会匹配环境变量 DATABASE_URL
    """

    # ─── pydantic-settings 配置 ─────────────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",                        # 从 .env 文件加载环境变量
        env_file_encoding="utf-8",              # .env 文件编码
        extra="ignore",                         # 忽略 .env 中多余的、未定义的变量（不报错）
    )

    # ─── 数据库配置 ──────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./data/werewolf.db"

    # ─── LLM 默认配置（所有玩家共用，向后兼容） ──────────────────
    llm_api_key: str = ""
    llm_base_url: str = "https://api.siliconflow.cn/v1"
    llm_model_name: str = "Qwen/Qwen3.5-Instruct"
    llm_temperature: float = 0.7
    llm_timeout: int = 30

    # ─── 按决策类型路由模型（阶段 3） ───────────────────────
    # 空字符串 = 不启用（回退现有链路，向后兼容）
    llm_action_simple_model: str = ""   # kill/verify/save/poison/vote/guard/hunter_shoot 使用
    llm_action_speech_model: str = ""   # speech/last_words 使用

    # ─── Prompt 膨胀治理（阶段 4a） ──────────────────────────
    prompt_history_budget: int = 3000   # 按字符数估算 token，超阈值时压缩历史

    # ─── AI 行动间隔（阶段 1 可配置） ────────────────────────
    ai_action_delay_night: float = 1.5
    ai_action_delay_day: float = 1.0
    ai_action_delay_vote: float = 0.8

    # ─── LLM 超时分级（阶段 L3） ───────────────────────────
    llm_timeout_simple: int = 15    # kill/verify/save/poison/vote/guard/hunter_shoot
    llm_timeout_speech: int = 45    # speech/last_words

    # ─── 多实例 LLM 配置（每个座位独立模型） ────────────────────
    # 座位1~12 各自的模型和温度（共用同一个 API Key + Base URL）
    llm_inst_1_model: str = ""
    llm_inst_1_temperature: float = 0.7
    llm_inst_2_model: str = ""
    llm_inst_2_temperature: float = 0.7
    llm_inst_3_model: str = ""
    llm_inst_3_temperature: float = 0.7
    llm_inst_4_model: str = ""
    llm_inst_4_temperature: float = 0.7
    llm_inst_5_model: str = ""
    llm_inst_5_temperature: float = 0.7
    llm_inst_6_model: str = ""
    llm_inst_6_temperature: float = 0.7
    llm_inst_7_model: str = ""
    llm_inst_7_temperature: float = 0.7
    llm_inst_8_model: str = ""
    llm_inst_8_temperature: float = 0.7
    llm_inst_9_model: str = ""
    llm_inst_9_temperature: float = 0.7
    llm_inst_10_model: str = ""
    llm_inst_10_temperature: float = 0.7
    llm_inst_11_model: str = ""
    llm_inst_11_temperature: float = 0.7
    llm_inst_12_model: str = ""
    llm_inst_12_temperature: float = 0.7

    # ─── CORS 跨域配置 ───────────────────────────────────────
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ─── 服务器配置 ──────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ─── 属性方法 ────────────────────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def data_dir(self) -> Path:
        d = Path("./data")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_llm_instance(self, seat_number: int) -> LlmInstance | None:
        """根据座位号返回对应的 LLM 实例配置

        如果该座位没有独立配置，返回 None（调用方应回退到默认配置）
        """
        model_attr = f"llm_inst_{seat_number}_model"
        temp_attr = f"llm_inst_{seat_number}_temperature"
        model = getattr(self, model_attr, "")
        if not model:
            return None
        temp = getattr(self, temp_attr, self.llm_temperature)
        return LlmInstance(model_name=model, temperature=temp, timeout=self.llm_timeout)

    def get_llm_instances_map(self) -> dict[int, LlmInstance]:
        """返回所有座位号 → LLM 实例的映射（仅包含有配置的座位）"""
        result = {}
        for seat in range(1, 13):
            inst = self.get_llm_instance(seat)
            if inst:
                result[seat] = inst
        return result


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例

    @lru_cache 确保整个应用生命周期内只创建一次 Settings 实例
    后续调用直接返回缓存对象，避免重复读取 .env 文件
    """
    return Settings()
