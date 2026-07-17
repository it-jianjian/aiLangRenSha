"""AI 狼人杀 — 全局配置管理

职责：集中管理所有环境变量和配置项
机制：通过 pydantic-settings 从 .env 文件和环境变量自动加载配置
优先级：环境变量 > .env 文件 > 代码中的默认值
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # 连接串格式: sqlite+aiosqlite:///./data/werewolf.db
    # - sqlite: 使用 SQLite 数据库（MVP 阶段够用，后续可切换 PostgreSQL）
    # - +aiosqlite: 异步驱动，让 SQLAlchemy 的 async 模式能在 SQLite 上工作
    # - ///./data/werewolf.db: 相对路径，数据文件在 backend/data/ 目录下
    database_url: str = "sqlite+aiosqlite:///./data/werewolf.db"

    # ─── LLM 模型配置 ────────────────────────────────────────
    # API Key: 模型服务的密钥（如 DashScope API Key），从 .env 读取，不硬编码
    llm_api_key: str = ""
    # API 地址: DeepSeek 提供 OpenAI-compatible 端点
    # 官网: https://platform.deepseek.com/
    llm_base_url: str = "https://api.deepseek.com/v1"
    # 模型名称: deepseek-chat (V3对话) / deepseek-reasoner (R1推理)
    llm_model_name: str = "deepseek-chat"
    # 温度参数: 控制 LLM 输出随机性，0.1=最确定, 2.0=最随机，0.7 适合对话场景
    llm_temperature: float = 0.7
    # 超时时间: 单次 LLM 调用最大等待秒数，超时后触发重试或降级
    llm_timeout: int = 60

    # ─── CORS 跨域配置 ───────────────────────────────────────
    # 允许哪些前端域名访问后端 API（逗号分隔多个）
    # - localhost:3000: Vite 默认开发服务器端口
    # - localhost:5173: Vite 新版默认端口
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # ─── 服务器配置 ──────────────────────────────────────────
    host: str = "0.0.0.0"                       # 监听地址，0.0.0.0 表示所有网卡
    port: int = 8000                            # 监听端口

    # ─── 属性方法（计算派生值） ──────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        """将逗号分隔的 CORS 源字符串解析为列表

        FastAPI 的 CORSMiddleware 需要 list[str] 格式，这里做转换
        例: "http://a.com,http://b.com" → ["http://a.com", "http://b.com"]
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def data_dir(self) -> Path:
        """获取数据目录路径，不存在则自动创建

        SQLite 数据库文件存放在这个目录下
        首次启动时如果目录不存在，会自动创建（mkdir parents=True）
        """
        d = Path("./data")
        d.mkdir(parents=True, exist_ok=True)    # parents=True 递归创建父目录
        return d


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例

    @lru_cache 确保整个应用生命周期内只创建一次 Settings 实例
    后续调用直接返回缓存对象，避免重复读取 .env 文件
    """
    return Settings()
