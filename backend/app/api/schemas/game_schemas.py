"""AI 狼人杀 — Pydantic API Schemas（请求/响应数据模型）

职责：定义所有 API 接口的输入输出数据结构
机制：Pydantic BaseModel 自动做数据验证和序列化

与 ORM Model（models/game.py）的区别：
- ORM Model: 对应数据库表结构，用于读写数据库
- API Schema: 对应 HTTP 请求/响应格式，用于前后端通信

同一个数据可能在两者之间转换，例如：
  前端 POST body → CreateGameRequest(Schema) → Game(ORM) → 数据库
  数据库 → Game(ORM) → GameSummary(Schema) → JSON Response → 前端
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ================================================================
# 通用响应
# ================================================================

class ApiResponse(BaseModel):
    """统一 API 响应包装

    所有接口返回统一格式：{code, message, data}
    - code=0: 成功
    - code!=0: 失败（具体错误码见各接口文档）
    - data: 业务数据（字典或 null）
    """
    code: int = 0                               # 0=成功，非0=错误码
    message: str = "ok"                         # 人类可读的消息
    data: Optional[dict] = None                 # 业务数据载荷


# ================================================================
# 对局相关（创建、列表、详情）
# ================================================================

class GameConfig(BaseModel):
    """对局配置（创建对局时的可选参数）"""
    model_name: str = Field(
        default="qwen-plus",
        description="LLM 模型名称，如 qwen-plus、qwen-turbo、chatglm-4"
    )
    temperature: float = Field(
        default=0.7,
        ge=0.1, le=2.0,                         # ge=最小值, le=最大值
        description="LLM 温度参数，控制输出随机性（0.1~2.0）"
    )


class CreateGameRequest(BaseModel):
    """创建对局请求体（POST /api/v1/games）

    前端提交的 JSON 示例：
    {
        "mode": "pure_ai",
        "config": {"model_name": "qwen-plus", "temperature": 0.7}
    }
    或混合模式：
    {
        "mode": "mixed",
        "player_name": "张三",
        "config": {"model_name": "qwen-plus"}
    }
    """
    mode: str = Field(
        description="游戏模式: pure_ai（纯AI对战）/ mixed（人类+AI混合）"
    )
    player_name: Optional[str] = Field(
        default=None,
        max_length=32,
        description="人类玩家名称（混合模式必填，纯AI模式可省略）"
    )
    config: GameConfig = Field(
        default_factory=GameConfig,
        description="对局配置（可选，省略则使用默认值）"
    )


class PlayerInfo(BaseModel):
    """玩家信息（在对局详情中返回）"""
    seat_number: int                            # 座位号 1-12
    player_name: str                            # 显示名称
    player_type: str                            # human / ai
    role: Optional[str] = None                  # 角色（仅对自己可见 或 游戏结束后可见）
    is_alive: bool = True                       # 是否存活
    llm_model_name: str = ""                    # 该 AI 玩家使用的模型名称


class GameSummary(BaseModel):
    """对局摘要（用于列表页展示，信息较少）"""
    game_id: str
    mode: str                                   # pure_ai / mixed
    status: str                                 # waiting / playing / finished
    winner: Optional[str] = None                # 获胜方（未结束时为 null）
    total_rounds: int = 0                       # 已完成回合数
    created_at: datetime                        # 创建时间
    finished_at: Optional[datetime] = None      # 结束时间（未结束时为 null）


class GameDetail(BaseModel):
    """对局详情（用于详情页，包含玩家列表和回合记录）"""
    game_id: str
    mode: str
    status: str
    current_round: int = 0                      # 当前进行到第几回合
    current_phase: Optional[str] = None         # 当前阶段（night/day）
    winner: Optional[str] = None
    end_reason: Optional[str] = None            # 结束原因
    players: list[PlayerInfo]                   # 6 个玩家的信息列表
    rounds: list[dict] = []                     # 回合详细数据列表
    model_name: str = ""                        # LLM 模型名（用于纯AI模式展示）


# ================================================================
# 操作相关（人类玩家在游戏中的操作）
# ================================================================

class NightActionRequest(BaseModel):
    """夜晚行动请求（POST /api/v1/games/{id}/actions/night）

    根据玩家角色不同，action_type 和含义不同：
    - 狼人: action_type="kill", target_seat=要击杀的座位号
    - 预言家: action_type="verify", target_seat=要查验的座位号
    - 女巫: action_type="save"(解药) 或 "poison"(毒药, 需 target_seat)
    - 女巫不用药: action_type="skip"
    """
    action_type: str = Field(description="行动类型: kill/verify/save/poison/skip")
    target_seat: Optional[int] = Field(default=None, description="目标座位号（kill/verify/poison 时需要）")


class SpeechRequest(BaseModel):
    """发言请求（POST /api/v1/games/{id}/actions/speech）"""
    content: str = Field(
        min_length=1, max_length=500,
        description="发言文本内容（1~500 字符）"
    )
    is_pk: bool = Field(
        default=False,
        description="是否为 PK 环节发言（平票后的额外发言）"
    )
    action_type: Literal["speech", "last_words"] = "speech"


class VoteRequest(BaseModel):
    """投票请求（POST /api/v1/games/{id}/actions/vote）"""
    target_seat: Optional[int] = Field(
        default=None,
        description="投票目标座位号，null 表示弃票"
    )
    is_pk_vote: bool = Field(
        default=False,
        description="是否为 PK 环节投票"
    )


# ================================================================
# 回放相关
# ================================================================

class ReplayStep(BaseModel):
    """回放步骤（一个游戏事件对应一步）"""
    step_index: int                             # 步骤序号（从 0 开始）
    round: Optional[int] = None                 # 所属回合号（用于前端按轮次分组）
    phase: str                                  # 所属阶段（night/day/system）
    event_type: str                             # 事件类型（对应 EventType 枚举）
    description: str                            # 人类可读的事件描述
    event_data: Optional[dict] = None           # 事件详细数据 JSON


class ReplayData(BaseModel):
    """回放数据（GET /api/v1/games/{id}/replay 返回）"""
    game_id: str
    total_steps: int                            # 总步骤数
    steps: list[ReplayStep]                     # 所有步骤列表
    role_mapping: dict                          # 角色映射 {座位号: 角色}，回放时揭示身份


# ================================================================
# WebSocket 消息
# ================================================================

class WSMessage(BaseModel):
    """WebSocket 消息格式

    服务端推送的消息统一格式，type 字段区分消息类型：
    - game_started: 对局开始
    - phase_change: 阶段切换
    - speech: 玩家发言
    - vote_result: 投票结果
    - ...（共 17 种服务端消息类型，详见技术方案 §四）
    """
    type: str                                   # 消息类型标识
    data: dict = {}                             # 消息载荷数据
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="消息时间戳（ISO8601 格式）"
    )
"""AI 狼人杀 — Pydantic API Schemas

所有 API 请求/响应模型定义。
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ── 通用响应 ──────────────────────────────────────────────

class ApiResponse(BaseModel):
    """统一 API 响应包装"""
    code: int = 0
    message: str = "ok"
    data: Optional[dict] = None


# ── 对局相关 ──────────────────────────────────────────────

class GameConfig(BaseModel):
    """对局配置"""
    model_name: str = Field(default="qwen-plus", description="LLM 模型名")
    temperature: float = Field(default=0.7, ge=0.1, le=2.0, description="温度参数")


class CreateGameRequest(BaseModel):
    """创建对局请求"""
    mode: str = Field(description="游戏模式: pure_ai / mixed")
    player_name: Optional[str] = Field(default=None, max_length=32, description="人类玩家名称(混合模式必填)")
    config: GameConfig = Field(default_factory=GameConfig)
    player_count: int = Field(default=6)
    roster_type: str = Field(default="official")
    roster: Optional[dict[str, int]] = None


class RosterUpdateRequest(BaseModel):
    player_count: int
    roster_type: str
    roster: dict[str, int]


class PlayerInfo(BaseModel):
    """玩家信息"""
    seat_number: int
    player_name: str
    player_type: str  # human / ai
    role: Optional[str] = None  # 仅对自己可见或游戏结束后可见
    is_alive: bool = True
    llm_model_name: str = ""  # 该 AI 玩家使用的模型名称


class GameSummary(BaseModel):
    """对局摘要（列表用）"""
    game_id: str
    mode: str
    status: str
    winner: Optional[str] = None
    total_rounds: int = 0
    created_at: datetime
    finished_at: Optional[datetime] = None


class GameDetail(BaseModel):
    """对局详情"""
    game_id: str
    mode: str
    status: str
    current_round: int = 0
    current_phase: Optional[str] = None
    winner: Optional[str] = None
    end_reason: Optional[str] = None
    players: list[PlayerInfo]
    rounds: list[dict] = []
    player_count: int = 6
    roster_type: str = "official"
    roster: dict[str, int] = {}
    roster_locked: bool = False
    model_name: str = ""                        # LLM 模型名（用于纯AI模式展示）


# ── 操作相关 ──────────────────────────────────────────────

class NightActionRequest(BaseModel):
    """夜晚行动请求"""
    action_type: str = Field(description="kill/verify/save/poison/skip")
    target_seat: Optional[int] = Field(default=None, description="目标座位号")


class SpeechRequest(BaseModel):
    """发言请求"""
    content: str = Field(min_length=1, max_length=500, description="发言内容")
    is_pk: bool = False
    action_type: Literal["speech", "last_words"] = "speech"


class VoteRequest(BaseModel):
    """投票请求"""
    target_seat: Optional[int] = Field(default=None, description="目标座位号, null=弃票")
    is_pk_vote: bool = False


# ── 回放相关 ──────────────────────────────────────────────

class ReplayStep(BaseModel):
    """回放步骤"""
    step_index: int
    round: Optional[int] = None
    phase: str
    event_type: str
    description: str
    event_data: Optional[dict] = None


class ReplayData(BaseModel):
    """回放数据"""
    game_id: str
    total_steps: int
    steps: list[ReplayStep]
    role_mapping: dict  # {seat: role}
    player_count: int = 6
    roster: dict[str, int] = {}
    winner: Optional[str] = None
    end_reason: Optional[str] = None


# ── WebSocket 消息 ────────────────────────────────────────

class WSMessage(BaseModel):
    """WebSocket 消息格式"""
    type: str
    data: dict = {}
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
