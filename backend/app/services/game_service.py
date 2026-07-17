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
import hashlib
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.game_schemas import CreateGameRequest, NightActionRequest, SpeechRequest, VoteRequest
from app.models.game import (
    Game, GamePlayer, GameRound, GameEvent, ChatMessage, Vote,
    GameMode, GameStatus, PlayerRole, PlayerType, EventType,
)
from app.services.game_rules import OFFICIAL_ROSTERS, validate_roster


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
AI_PERSONAS = [
    "冷静分析师", "热情社交家", "逻辑推理者", "直觉玩家", "保守策略家", "冒险挑战者",
    "细节观察者", "强势领袖", "谨慎求证者", "幽默搅局者", "沉默思考者", "风险预判者",
]


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

    async def terminate_unrecoverable_playing_games(self) -> int:
        """结束重启后失去内存执行器、且无法安全续跑的对局。"""
        result = await self.db.execute(
            select(Game).where(Game.status == GameStatus.PLAYING)
        )
        games = result.scalars().all()
        if not games:
            return 0

        finished_at = datetime.now()
        for game in games:
            game.status = GameStatus.FINISHED
            game.end_reason = "interrupted_by_restart"
            game.finished_at = finished_at
        await self.db.flush()
        return len(games)

    # ─── 创建对局 ──────────────────────────────────────────

    async def create_game(self, request: CreateGameRequest) -> tuple[Game, str, str | None]:
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
        roster = request.roster or dict(OFFICIAL_ROSTERS.get(request.player_count, OFFICIAL_ROSTERS[6]))
        errors = validate_roster(request.player_count, request.roster_type, roster)
        if errors:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail={"code": 40001, "errors": errors})
        owner_token = secrets.token_urlsafe(32)
        player_token = secrets.token_urlsafe(32) if request.mode == GameMode.MIXED else None
        game = Game(
            mode=request.mode,
            config_json=json.dumps(request.config.model_dump()),  # 配置序列化为 JSON 存储
            player_count=request.player_count,
            roster_type=request.roster_type,
            roster_json=json.dumps(roster),
            owner_token_hash=self._token_hash(owner_token),
        )
        self.db.add(game)
        await self.db.flush()                     # flush 生成 game.id，但还不 commit

        for player_spec in self._build_player_specs(
            request.mode, request.player_name, roster,
            human_access_token_hash=self._token_hash(player_token or "") if player_token else None,
        ):
            player = GamePlayer(
                game_id=game.id,
                **player_spec,
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
        return game, owner_token, player_token

    async def update_roster(self, game: Game, owner_token: str, player_count: int, roster_type: str, roster: dict[str, int]) -> list[str]:
        self._assert_owner(game, owner_token)
        if game.status != GameStatus.WAITING or game.roster_locked_at:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="对局已开始，配置已锁定")
        errors = validate_roster(player_count, roster_type, roster)
        game.player_count = player_count
        game.roster_type = roster_type
        game.roster_json = json.dumps(roster)
        human = next((player for player in game.players if player.player_type == PlayerType.HUMAN), None)
        human_name = human.player_name if human else None
        human_access_token_hash = human.access_token_hash if human else None
        await self.db.execute(delete(GamePlayer).where(GamePlayer.game_id == game.id))
        await self.db.flush()
        for player_spec in self._build_player_specs(
            game.mode, human_name, roster, human_access_token_hash=human_access_token_hash,
        ):
            self.db.add(GamePlayer(game_id=game.id, **player_spec))
        await self.db.flush()
        if game.mode == GameMode.MIXED:
            human_player = (await self.db.execute(
                select(GamePlayer).where(
                    GamePlayer.game_id == game.id,
                    GamePlayer.player_type == PlayerType.HUMAN,
                )
            )).scalar_one()
            game.human_player_id = human_player.id
        return errors

    async def reset_official_roster(self, game: Game, owner_token: str) -> dict[str, int]:
        roster = dict(OFFICIAL_ROSTERS[game.player_count])
        await self.update_roster(game, owner_token, game.player_count, "official", roster)
        return roster

    # ─── 启动对局 ──────────────────────────────────────────

    async def start_game(self, game: Game, owner_token: str):
        """启动对局

        业务流程：
        1. 将对局状态从 waiting 改为 playing
        2. 记录开始时间
        3. 用 asyncio.create_task 异步启动 LangGraph 游戏流程
           - create_task 会立即返回，游戏在后台运行
           - HTTP 响应不需要等游戏结束
        """
        self._assert_owner(game, owner_token)
        errors = validate_roster(game.player_count, game.roster_type, json.loads(game.roster_json))
        if errors:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail={"code": 40002, "errors": errors})
        now = datetime.now()
        result = await self.db.execute(
            update(Game)
            .where(
                Game.id == game.id,
                Game.status == GameStatus.WAITING,
                Game.roster_locked_at.is_(None),
            )
            .values(status=GameStatus.PLAYING, started_at=now, roster_locked_at=now)
        )
        if result.rowcount != 1:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="对局已开始")
        game.status = GameStatus.PLAYING
        game.started_at = now
        game.roster_locked_at = now

        # 异步启动游戏流程（不阻塞 HTTP 响应）
        # asyncio.create_task 将协程放入事件循环后台执行
        asyncio.create_task(self._run_game_flow(game.id))

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _build_player_specs(
        mode: str,
        human_name: str | None,
        roster: dict[str, int],
        human_access_token_hash: str | None = None,
    ) -> list[dict]:
        """从最终阵容快照生成完整座位，创建和编辑阵容共用此唯一来源。"""
        roles = [role for role, count in roster.items() for _ in range(count)]
        random.shuffle(roles)
        personas = AI_PERSONAS.copy()
        random.shuffle(personas)
        specs = []
        for seat, role in enumerate(roles, start=1):
            is_human = mode == GameMode.MIXED and seat == 1
            spec = {
                "seat_number": seat,
                "player_type": PlayerType.HUMAN if is_human else PlayerType.AI,
                "role": role,
                "player_name": human_name if is_human else f"AI-{personas[seat - 1]}",
                "ai_persona": None if is_human else personas[seat - 1],
            }
            if is_human:
                spec["access_token_hash"] = human_access_token_hash
            specs.append(spec)
        return specs

    def _assert_owner(self, game: Game, owner_token: str) -> None:
        from fastapi import HTTPException
        if not owner_token or not secrets.compare_digest(game.owner_token_hash or "", self._token_hash(owner_token)):
            raise HTTPException(status_code=403, detail="仅房主可修改或开始对局")

    async def _run_game_flow(self, game_id: str):
        """运行游戏主流程（LangGraph StateGraph）

        这里调用了 LangGraph 的游戏流程编排模块
        P2 阶段会实现完整的 StateGraph 逻辑（夜晚→白天→投票→胜负检查循环）
        当前为占位实现
        """
        from app.graphs.game_flow import run_game
        await run_game(game_id)

    # ─── 人类玩家操作（通过 HumanActionBridge 桥接） ─────

    async def submit_night_action(self, game_id: str, player_seat: int, request: NightActionRequest):
        """人类玩家提交夜晚行动

        实现机制：
        - 游戏流程在人类回合通过 human_bridge.wait_for_action() 阻塞
        - 此接口收到操作后，通过 human_bridge.submit_action() 唤醒游戏
        """
        from app.services.human_action_bridge import human_bridge
        human_bridge.submit_action(game_id, player_seat, {
            "action_type": request.action_type,
            "target_seat": request.target_seat,
        })

    async def submit_speech(self, game_id: str, player_seat: int, request: SpeechRequest):
        """人类玩家提交发言"""
        from app.services.human_action_bridge import human_bridge
        human_bridge.submit_action(game_id, player_seat, {
            "action_type": request.action_type,
            "content": request.content,
        })

    async def submit_vote(self, game_id: str, player_seat: int, request: VoteRequest):
        """人类玩家提交投票"""
        from app.services.human_action_bridge import human_bridge
        human_bridge.submit_action(game_id, player_seat, {
            "action_type": "vote",
            "target_seat": request.target_seat,
        })
