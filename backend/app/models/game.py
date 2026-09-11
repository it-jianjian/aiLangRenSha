"""AI 狼人杀 — SQLAlchemy ORM 数据模型

对应技术方案 §三 表结构设计（8 张表）。
本文件定义了所有数据库表的 ORM 映射类。

表关系总览：
  Game 1──N GamePlayer     (一局游戏有多个玩家)
  Game 1──N GameRound      (一局游戏有多个回合)
  Game 1──N GameEvent      (一局游戏有多个事件)
  Game 1──N ChatMessage    (一局游戏有多条发言消息)
  Game 1──N Vote           (一局游戏有多次投票)
  Game 1──N AgentLog       (一局游戏有多条 AI 推理日志)
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


def _uuid() -> str:
    """生成 UUID 字符串作为主键

    使用 UUID 而非自增 ID 的好处：
    - 分布式环境下不会冲突
    - 不暴露业务信息（无法通过 ID 推断对局数量）
    - 前端可以在创建前预生成 ID
    """
    return str(uuid.uuid4())


# ================================================================
# 枚举常量类
# 用 class 而非 enum.Enum 的原因：SQLite 存储字符串更直观，调试友好
# 在代码中引用方式：GameMode.PURE_AI、PlayerRole.WEREWOLF
# ================================================================

class GameMode:
    """游戏模式"""
    PURE_AI = "pure_ai"     # 纯 AI 对战：6 个 AI Agent 自动完成一局
    MIXED = "mixed"         # 人类 + AI 混合：1 名人类玩家 + 5 个 AI


class GameStatus:
    """对局状态"""
    WAITING = "waiting"     # 等待开始：对局已创建，等待点击"开始"
    PLAYING = "playing"     # 进行中：游戏流程正在运行
    FINISHED = "finished"   # 已结束：游戏结束，可查看回放


class PlayerRole:
    """玩家角色（6人局配置：2狼人 + 2村民 + 1预言家 + 1女巫）"""
    WEREWOLF = "werewolf"   # 狼人：夜晚击杀目标，互相知道身份
    VILLAGER = "villager"   # 村民：无技能，靠推理和投票参与
    SEER = "seer"           # 预言家：夜晚查验一名玩家是狼人还是好人
    WITCH = "witch"         # 女巫：拥有解药和毒药，每晚只能用一种
    HUNTER = "hunter"       # 猎人：狼杀或放逐死亡时可带走一人
    GUARD = "guard"         # 守卫：每夜守护一名玩家，不能连续守同一人


class PlayerType:
    """玩家类型"""
    HUMAN = "human"         # 人类玩家：通过前端界面操作
    AI = "ai"               # AI 玩家：由 LangGraph Agent 自动决策


class Winner:
    """获胜阵营"""
    WEREWOLF = "werewolf"   # 狼人阵营获胜
    VILLAGER = "villager"   # 好人阵营获胜


class EventType:
    """游戏事件类型（用于 GameEvent 表的 event_type 字段）

    每一局游戏的完整过程会被拆解为一系列事件，
    按时间顺序记录到 GameEvent 表，支持对局回放。
    """
    # ─── 夜晚事件 ───
    ROLE_ASSIGN = "role_assign"         # 角色分配（游戏开始时）
    NIGHT_KILL = "night_kill"           # 狼人选择击杀目标
    NIGHT_VERIFY = "night_verify"       # 预言家选择查验目标
    NIGHT_SAVE = "night_save"           # 女巫使用解药
    NIGHT_POISON = "night_poison"       # 女巫使用毒药
    NIGHT_SETTLE = "night_settle"       # 夜晚结算（综合判定死亡名单）
    NIGHT_GUARD = "night_guard"
    HUNTER_REVENGE = "hunter_revenge"
    HUNTER_SHOT = "hunter_shot"

    # ─── 白天事件 ───
    DEATH_ANNOUNCE = "death_announce"   # 公布夜晚死亡信息
    LAST_WORDS = "last_words"           # 死亡玩家遗言（首夜无遗言）
    SPEECH = "speech"                   # 存活玩家公开发言
    VOTE = "vote"                       # 存活玩家投票
    VOTE_RESULT = "vote_result"         # 投票结果公布
    PK_ANNOUNCE = "pk_announce"         # 平票进入 PK 公告
    PK_SPEECH = "pk_speech"             # PK 环节发言
    PK_VOTE = "pk_vote"                 # PK 环节重新投票
    ELIMINATE = "eliminate"             # 淘汰玩家

    # ─── 系统事件 ───
    VICTORY_CHECK = "victory_check"     # 胜负检查
    GAME_OVER = "game_over"             # 游戏结束
    PHASE_CHANGE = "phase_change"       # 阶段切换（夜晚→白天→夜晚...）
    TIMEOUT = "timeout"                 # 操作超时


# ================================================================
# 表定义
# ================================================================

class Game(Base):
    """对局主表 — 存储一局游戏的核心信息

    一局游戏对应一条 Game 记录，从创建到结束的全部状态变化都在这里。
    """
    __tablename__ = "games"

    id = Column(String(36), primary_key=True, default=_uuid)
    mode = Column(String(16), nullable=False, comment="模式: pure_ai/mixed")
    status = Column(String(16), nullable=False, default=GameStatus.WAITING, comment="waiting/playing/finished")
    winner = Column(String(16), nullable=True, comment="werewolf/villager，游戏结束后填入")
    end_reason = Column(String(32), nullable=True, comment="结束原因，如 all_werewolf_dead / deadlock_3peace")
    total_rounds = Column(Integer, nullable=False, default=0, comment="已完成的回合数")
    human_player_id = Column(String(36), nullable=True, comment="人类玩家ID（仅混合模式）")
    config_json = Column(Text, nullable=False, default="{}", comment="JSON格式配置，如模型名、温度等")
    player_count = Column(Integer, nullable=False, default=6)
    roster_type = Column(String(16), nullable=False, default="official")
    roster_json = Column(Text, nullable=False, default='{"werewolf":2,"villager":2,"seer":1,"witch":1,"hunter":0,"guard":0}')
    roster_locked_at = Column(DateTime, nullable=True)
    owner_token_hash = Column(String(128), nullable=True)
    owner_user_id = Column(String(36), nullable=True, comment="创建者用户ID（可选登录）")
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="创建时间")
    started_at = Column(DateTime, nullable=True, comment="开始时间")
    finished_at = Column(DateTime, nullable=True, comment="结束时间")
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="最后更新时间")

    # ─── ORM 关系（自动关联查询） ───
    # back_populates: 双向关联，从 Game 可以访问子表，从子表也可以访问 Game
    # order_by: 查询时自动排序
    players = relationship("GamePlayer", back_populates="game", order_by="GamePlayer.seat_number")
    rounds = relationship("GameRound", back_populates="game", order_by="GameRound.round_number")
    events = relationship("GameEvent", back_populates="game")
    messages = relationship("ChatMessage", back_populates="game")
    agent_logs = relationship("AgentLog", back_populates="game")
    agent_steps = relationship("AgentStep", back_populates="game")

    # ─── 索引 ───
    __table_args__ = (
        Index("idx_games_status", "status"),                # 按状态查询（如找进行中的对局）
        Index("idx_games_created_at", created_at.desc()),   # 按创建时间倒序（列表页默认排序）
    )


class GamePlayer(Base):
    """玩家表 — 一局游戏中的 6 个座位

    每个座位一条记录，包含：
    - 座位号（1-6）
    - 角色（狼人/村民/预言家/女巫）
    - 玩家类型（人类/AI）
    - 存活状态（死亡时记录死亡信息）
    """
    __tablename__ = "game_players"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False, comment="所属对局")
    seat_number = Column(Integer, nullable=False, comment="座位号 1-6")
    player_type = Column(String(8), nullable=False, comment="human/ai")
    role = Column(String(16), nullable=False, comment="werewolf/villager/seer/witch")
    player_name = Column(String(32), nullable=False, comment="显示名称，如 'AI-冷静分析师' 或人类昵称")
    is_alive = Column(Boolean, nullable=False, default=True, comment="是否存活")
    death_round = Column(Integer, nullable=True, comment="死亡轮次（第几回合死的）")
    death_phase = Column(String(8), nullable=True, comment="死亡阶段: night/day")
    death_reason = Column(String(32), nullable=True, comment="死因: killed_by_werewolf/poisoned/voted_out")
    ai_persona = Column(String(64), nullable=True, comment="AI 人设标识，如'冷静分析师'，影响 Prompt 风格")
    access_token_hash = Column(String(128), nullable=True, comment="人类玩家一次性访问凭据摘要")
    user_id = Column(String(36), nullable=True, comment="该座位绑定的用户ID（可选登录，人类席）")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    game = relationship("Game", back_populates="players")

    __table_args__ = (
        # 同一局游戏内座位号不能重复（一个座位只能有一个玩家）
        UniqueConstraint("game_id", "seat_number"),
    )


class GameRound(Base):
    """回合表 — 记录每一轮（夜晚+白天）的详细数据

    每一轮（round）包含：
    - 夜晚：狼人击杀、预言家查验、女巫用药
    - 白天：发言、投票、淘汰
    """
    __tablename__ = "game_rounds"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False)
    round_number = Column(Integer, nullable=False, comment="回合编号，从 1 开始")

    # ─── 夜晚数据 ───
    werewolf_target_seat = Column(Integer, nullable=True, comment="狼人最终击杀目标座位号")
    werewolf_votes_json = Column(Text, nullable=True, comment="狼人各自选择的 JSON，如 {1:3, 2:5}")
    werewolf_agreement = Column(String(16), nullable=True, comment="协商结果: unanimous(一致)/random(随机)/human_priority(人类优先)")
    seer_target_seat = Column(Integer, nullable=True, comment="预言家查验目标座位号")
    seer_result = Column(String(8), nullable=True, comment="查验结果: werewolf/villager")
    witch_action = Column(String(16), nullable=True, comment="女巫行动: save(解药)/poison(毒药)/none(不用)")
    witch_target_seat = Column(Integer, nullable=True, comment="女巫毒药目标（仅 poison 时有值）")
    night_deaths_json = Column(Text, nullable=True, comment="夜晚死亡列表 JSON")
    guard_target_seat = Column(Integer, nullable=True)
    hunter_shot_seat = Column(Integer, nullable=True)
    hunter_shot_trigger = Column(String(32), nullable=True)

    # ─── 遗言标记 ───
    # PRD 规则：首夜死亡无遗言，第二夜起有遗言
    has_last_words_night = Column(Boolean, nullable=False, default=False, comment="夜晚死者是否有遗言(首夜=False)")

    # ─── 白天数据 ───
    eliminated_seat = Column(Integer, nullable=True, comment="投票淘汰的座位号")
    is_pk_round = Column(Boolean, nullable=False, default=False, comment="是否经历了 PK（平票重投）")
    is_peace_day = Column(Boolean, nullable=False, default=False, comment="是否平安日（无人淘汰）")

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    game = relationship("Game", back_populates="rounds")

    __table_args__ = (
        UniqueConstraint("game_id", "round_number"),   # 同一局游戏内回合号唯一
    )


class GameEvent(Base):
    """事件表 — 完整事件链（对局回放的底层数据）

    每一个游戏动作（击杀、查验、发言、投票等）都会在这里创建一条记录，
    按 created_at 排序就是完整的游戏时间线，前端回放功能基于此表。
    """
    __tablename__ = "game_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False)
    round_number = Column(Integer, nullable=False, comment="所属回合")
    phase = Column(String(8), nullable=False, comment="night/day/system")
    event_type = Column(String(32), nullable=False, comment="事件类型，对应 EventType 枚举")
    seat_number = Column(Integer, nullable=True, comment="执行者座位号（如发言者、投票者）")
    event_data = Column(Text, nullable=True, comment="事件详细 JSON（如发言内容、投票目标等）")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    game = relationship("Game", back_populates="events")

    __table_args__ = (
        Index("idx_events_game_round", "game_id", "round_number"),  # 按局+回合查询
    )


class ChatMessage(Base):
    """发言消息表 — 存储所有公开发言（含遗言、PK发言）

    与 GameEvent 的区别：
    - GameEvent 记录所有类型事件（含系统事件）
    - ChatMessage 只记录"说话"内容，用于前端消息流展示
    """
    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False)
    round_number = Column(Integer, nullable=False, comment="所属回合")
    seat_number = Column(Integer, nullable=False, comment="发言者座位号")
    content = Column(Text, nullable=False, comment="发言文本内容")
    is_pk = Column(Boolean, nullable=False, default=False, comment="是否为 PK 环节发言")
    is_last_words = Column(Boolean, nullable=False, default=False, comment="是否为遗言")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    game = relationship("Game", back_populates="messages")

    __table_args__ = (
        Index("idx_chat_game_round", "game_id", "round_number"),
    )


class Vote(Base):
    """投票表 — 记录每一票的来源和目标

    每次投票环节（含 PK 重投），每个存活玩家产生一条记录。
    target_seat=null 表示弃票。
    """
    __tablename__ = "votes"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False)
    round_number = Column(Integer, nullable=False, comment="所属回合")
    voter_seat = Column(Integer, nullable=False, comment="投票者座位号")
    target_seat = Column(Integer, nullable=True, comment="被投者座位号，null=弃票")
    is_pk = Column(Boolean, nullable=False, default=False, comment="是否为 PK 环节投票")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        # 同一回合中，同一玩家在同类型投票中只能投一次
        UniqueConstraint("game_id", "round_number", "voter_seat", "is_pk"),
    )


class AgentLog(Base):
    """Agent 日志表 — AI 推理完整记录（审计 + 研究用）

    每次 AI Agent 做决策时，会记录：
    - context_json: 传给 Agent 的完整上下文（信息隔离审计）
    - prompt_text: 实际发给 LLM 的完整 Prompt
    - llm_raw_output: LLM 的原始输出文本
    - parsed_decision: 解析后的结构化决策
    - is_fallback: 是否因为超时/解析失败而降级为随机选择
    - latency_ms: LLM 调用耗时（毫秒）

    这个表是核心卖点之一：可用于分析 AI 的推理策略质量
    """
    __tablename__ = "agent_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False)
    round_number = Column(Integer, nullable=False)
    seat_number = Column(Integer, nullable=False, comment="Agent 座位号")
    action_type = Column(String(32), nullable=False, comment="决策类型: kill/verify/save/poison/speech/vote/last_words")
    context_json = Column(Text, nullable=True, comment="完整 context JSON（信息隔离审计用）")
    prompt_text = Column(Text, nullable=True, comment="实际发送的 Prompt 全文")
    llm_raw_output = Column(Text, nullable=True, comment="LLM 原始返回文本")
    parsed_decision = Column(Text, nullable=True, comment="解析后的决策 JSON")
    is_fallback = Column(Boolean, nullable=False, default=False, comment="是否降级随机决策（超时或解析失败）")
    latency_ms = Column(Integer, nullable=True, comment="LLM 调用耗时毫秒数")
    prompt_tokens = Column(Integer, nullable=True, comment="LLM 请求消耗的 prompt token 数")
    completion_tokens = Column(Integer, nullable=True, comment="LLM 请求消耗的 completion token 数")
    model_name = Column(String(64), nullable=True, comment="实际使用的模型名称")
    deliberation_round = Column(Integer, nullable=True, comment="狼队协商轮次: 1=首表态 2=修订/坚持; 非协商为 NULL")
    critique_result = Column(Text, nullable=True, comment="发言审稿单摘要 JSON: {risk_level, issue_types, fix_hint}; 非批评为 NULL")
    revised = Column(Boolean, nullable=True, comment="该发言是否经 revise 修订; 非批评为 NULL")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    game = relationship("Game", back_populates="agent_logs")
    steps = relationship("AgentStep", back_populates="agent_log", order_by="AgentStep.step_index")

    __table_args__ = (
        Index("idx_agent_logs_game_seat", "game_id", "seat_number"),  # 按局+座位查询
    )


class AgentStep(Base):
    """ReAct Agent 步骤轨迹表 — 记录每次 ReAct 决策的完整 Thought/Action/Observation 轨迹

    与 AgentLog 一对多关系：一次决策（AgentLog）包含多个步骤（AgentStep）。
    """
    __tablename__ = "agent_steps"

    id = Column(String(36), primary_key=True, default=_uuid)
    agent_log_id = Column(String(36), ForeignKey("agent_logs.id"), nullable=False, comment="所属 AgentLog 记录")
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False, comment="冗余字段，便于按局查询")
    step_index = Column(Integer, nullable=False, comment="步骤序号（0, 1, 2...）")
    step_type = Column(String(16), nullable=False, comment="thought/tool_call/observation/final")
    content = Column(Text, nullable=False, comment="步骤内容（推理文本/工具调用参数/观察结果/最终决策）")
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    game = relationship("Game", back_populates="agent_steps")
    agent_log = relationship("AgentLog", back_populates="steps")

    __table_args__ = (
        Index("idx_agent_steps_game_log", "game_id", "agent_log_id"),
    )


class GameReview(Base):
    """对局复盘表 — 缓存 AI 复盘点评结果（按需生成）

    一局对局至多一条复盘记录（game_id 唯一）。胜率曲线为实时规则计算不入表，
    此表只缓存 LLM 生成的点评 JSON，避免重复消耗 token；
    is_fallback=True 表示 LLM 不可用/解析失败时的降级占位。
    """
    __tablename__ = "game_reviews"

    id = Column(String(36), primary_key=True, default=_uuid)
    game_id = Column(String(36), ForeignKey("games.id"), nullable=False, unique=True, comment="所属对局（唯一）")
    review_json = Column(Text, nullable=False, comment="AI 复盘点评结构化 JSON")
    model_name = Column(String(64), nullable=True, comment="生成所用模型名")
    prompt_tokens = Column(Integer, nullable=True, comment="LLM 请求 prompt token 数")
    completion_tokens = Column(Integer, nullable=True, comment="LLM 请求 completion token 数")
    is_fallback = Column(Boolean, nullable=False, default=False, comment="是否降级（LLM 不可用/解析失败）")
    generated_at = Column(DateTime, nullable=False, server_default=func.now(), comment="生成时间")

    game = relationship("Game")


class CustomModel(Base):
    """用户自定义模型池 — 前端可自行添加模型名/API Key/Base URL，免改服务器配置重启。"""
    __tablename__ = "custom_models"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(128), nullable=False, unique=True, comment="模型名（唯一，供下拉选择）")
    api_key = Column(String(512), nullable=False, comment="该模型的 API Key")
    base_url = Column(String(256), nullable=False, comment="OpenAI-compatible Base URL")
    temperature = Column(Float, nullable=True, comment="可选温度，缺省用全局")
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class User(Base):
    """用户账号（可选登录）— 注册/登录/个人资料/我的对局/战绩。"""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    username = Column(String(64), nullable=False, unique=True, comment="登录名（唯一）")
    password_hash = Column(String(256), nullable=False, comment="pbkdf2 哈希")
    nickname = Column(String(64), nullable=True, comment="昵称")
    avatar = Column(String(16), nullable=True, comment="头像（emoji 或单字）")
    bio = Column(String(256), nullable=True, comment="个人简介")
    email = Column(String(128), nullable=True, comment="邮箱（可选）")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
