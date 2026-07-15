"""AI 狼人杀 — 游戏服务层（业务编排）

职责：封装游戏业务逻辑，连接 API 层和数据层
架构位置：API Router → GameService（本文件）→ ORM Model → 数据库

核心职责：
1. 创建对局 + 初始化玩家
2. 启动游戏流程（异步调用 LangGraph）
3. 接收人类玩家操作（P2 阶段实现）

与 LangGraph 的关系：
- GameService 负责"管理对局"（创建、开始、操作）
- LangGraph 负责"运行对局"（夜晚→白天→投票→胜负检查 的循环流程）
- GameService.start_game() 会异步启动 LangGraph 的 run_game()
"""

import asyncio
import json
import random
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.game_schemas import CreateGameRequest, NightActionRequest, SpeechRequest, VoteRequest
from app.models.game import (
    Game, GamePlayer, GameRound, GameEvent, ChatMessage, Vote,
    GameMode, GameStatus, PlayerRole, PlayerType, EventType,
)


# ================================================================
# 游戏配置常量
# ================================================================

# 6 人局角色配置（PRD §F02）
# 配比：2 狼人 + 2 村民 + 1 预言家 + 1 女巫
# 创建对局时随机打乱后分配给 6 个座位
ROLES_6_PLAYERS = [
    PlayerRole.WEREWOLF, PlayerRole.WEREWOLF,   # 2 个狼人
    PlayerRole.VILLAGER, PlayerRole.VILLAGER,   # 2 个村民
    PlayerRole.SEER,                             # 1 个预言家
    PlayerRole.WITCH,                            # 1 个女巫
]

# AI 人设列表（每个 AI 玩家随机分配一个人设）
# 人设会影响 LangChain PromptTemplate 中的角色风格，让 AI 发言更有个性
# 例如："冷静分析师" 会倾向理性推理，"热情社交家" 会更多情感表达
AI_PERSONAS = ["冷静分析师", "热情社交家", "逻辑推理者", "直觉玩家", "保守策略家", "冒险挑战者"]


class GameService:
    """游戏业务服务

    每个 API 请求创建一个 GameService 实例，传入数据库会话
    所有数据库操作通过这个会话完成
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ─── 查询方法 ──────────────────────────────────────────

    async def get_active_game(self) -> Optional[Game]:
        """获取当前正在进行的对局

        用于 MVP 限制：同一时刻只能有 1 个对局运行
        返回 None 表示当前无进行中的对局，可以创建新的
        """
        result = await self.db.execute(
            select(Game).where(Game.status == GameStatus.PLAYING)
        )
        return result.scalar_one_or_none()

    async def get_game_or_404(self, game_id: str) -> Game:
        """获取对局，不存在则抛出 404 异常

        同时加载关联的 players 列表（selectinload 避免 N+1 查询）
        """
        from fastapi import HTTPException
        result = await self.db.execute(
            select(Game)
            .options(selectinload(Game.players))
            .where(Game.id == game_id)
        )
        game = result.scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="对局不存在")
        return game

    # ─── 创建对局 ──────────────────────────────────────────

    async def create_game(self, request: CreateGameRequest) -> Game:
        """创建对局 + 初始化 6 个玩家

        业务流程：
        1. 创建 Game 记录（状态=waiting）
        2. 随机打乱角色列表和 AI 人设列表
        3. 为座位 1-6 各创建一个 GamePlayer 记录
           - 混合模式：座位 1 为人类玩家，其余为 AI
           - 纯 AI 模式：全部为 AI 玩家
        4. 混合模式下记录人类玩家的 ID（用于后续操作校验）
        """
        # ─── 创建对局主记录 ───
        game = Game(
            mode=request.mode,
            config_json=json.dumps(request.config.model_dump()),  # 配置序列化为 JSON 存储
        )
        self.db.add(game)
        await self.db.flush()                     # flush 生成 game.id，但还不 commit

        # ─── 随机分配角色和人设 ───
        roles = ROLES_6_PLAYERS.copy()
        random.shuffle(roles)                     # 打乱角色顺序
        personas = AI_PERSONAS.copy()
        random.shuffle(personas)                  # 打乱人设顺序

        # ─── 创建 6 个玩家 ───
        for seat in range(1, 7):
            # 混合模式下座位 1 固定为人类玩家
            is_human = (request.mode == GameMode.MIXED and seat == 1)
            player = GamePlayer(
                game_id=game.id,
                seat_number=seat,
                player_type=PlayerType.HUMAN if is_human else PlayerType.AI,
                role=roles[seat - 1],             # 分配随机角色
                # 人类玩家用提交的名字，AI 玩家用 "AI-{人设}" 格式
                player_name=request.player_name if is_human else f"AI-{personas[seat - 1]}",
                ai_persona=None if is_human else personas[seat - 1],
            )
            self.db.add(player)

        # ─── 记录人类玩家 ID（混合模式） ───
        if request.mode == GameMode.MIXED:
            result = await self.db.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == game.id,
                    GamePlayer.player_type == PlayerType.HUMAN,
                )
            )
            human = result.scalar_one()
            game.human_player_id = human.id       # 后续操作时用此 ID 验证是否为人类玩家

        await self.db.flush()
        return game

    # ─── 启动对局 ──────────────────────────────────────────

    async def start_game(self, game: Game):
        """启动对局

        业务流程：
        1. 将对局状态从 waiting 改为 playing
        2. 记录开始时间
        3. 用 asyncio.create_task 异步启动 LangGraph 游戏流程
           - create_task 会立即返回，游戏在后台运行
           - HTTP 响应不需要等游戏结束
        """
        from datetime import datetime
        game.status = GameStatus.PLAYING
        game.started_at = datetime.now()
        await self.db.flush()

        # 异步启动游戏流程（不阻塞 HTTP 响应）
        # asyncio.create_task 将协程放入事件循环后台执行
        asyncio.create_task(self._run_game_flow(game.id))

    async def _run_game_flow(self, game_id: str):
        """运行游戏主流程（LangGraph StateGraph）

        这里调用了 LangGraph 的游戏流程编排模块
        P2 阶段会实现完整的 StateGraph 逻辑（夜晚→白天→投票→胜负检查循环）
        当前为占位实现
        """
        from app.graphs.game_flow import run_game
        await run_game(game_id)

    # ─── 人类玩家操作（通过 HumanActionBridge 桥接） ─────

    async def submit_night_action(self, game_id: str, request: NightActionRequest):
        """人类玩家提交夜晚行动

        实现机制：
        - 游戏流程在人类回合通过 human_bridge.wait_for_action() 阻塞
        - 此接口收到操作后，通过 human_bridge.submit_action() 唤醒游戏
        """
        from app.services.human_action_bridge import human_bridge
        human_bridge.submit_action(game_id, {
            "action_type": request.action_type,
            "target_seat": request.target_seat,
        })

    async def submit_speech(self, game_id: str, request: SpeechRequest):
        """人类玩家提交发言"""
        from app.services.human_action_bridge import human_bridge
        human_bridge.submit_action(game_id, {
            "content": request.content,
        })

    async def submit_vote(self, game_id: str, request: VoteRequest):
        """人类玩家提交投票"""
        from app.services.human_action_bridge import human_bridge
        human_bridge.submit_action(game_id, {
            "target_seat": request.target_seat,
        })
