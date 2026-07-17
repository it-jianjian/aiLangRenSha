"""AI 狼人杀 — 对局 API 路由

职责：处理对局相关的 HTTP 请求
路由前缀：/api/v1/games（在 main.py 中注册）

包含的接口：
  POST   /api/v1/games              — 创建对局
  GET    /api/v1/games              — 对局列表（分页）
  GET    /api/v1/games/{id}         — 对局详情
  POST   /api/v1/games/{id}/start   — 开始对局
  POST   /api/v1/games/{id}/actions/night  — 夜晚行动（人类玩家）
  POST   /api/v1/games/{id}/actions/speech — 提交发言（人类玩家）
  POST   /api/v1/games/{id}/actions/vote   — 提交投票（人类玩家）

架构分层：
  前端请求 → API 路由（本文件）→ GameService（业务逻辑）→ ORM Model → 数据库
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.game_schemas import (
    ApiResponse,
    CreateGameRequest,
    GameDetail,
    GameSummary,
    NightActionRequest,
    PlayerInfo,
    SpeechRequest,
    VoteRequest,
    RosterUpdateRequest,
)
from app.db.session import get_db
from app.models.game import Game, GameEvent, GamePlayer, GameMode, GameStatus, PlayerType
from app.services.game_service import GameService
from app.services.public_events import to_public_event

# 创建路由器实例，在 main.py 中通过 include_router 注册到 FastAPI
router = APIRouter()


# ─── 创建对局 ─────────────────────────────────────────────
# POST /api/v1/games
# 请求体示例: {"mode": "pure_ai", "config": {"model_name": "qwen-plus"}}
@router.post("", response_model=ApiResponse)
async def create_game(
    request: CreateGameRequest,                         # Pydantic 自动验证请求体
    db: AsyncSession = Depends(get_db),                 # Depends 注入数据库会话
):
    """创建对局

    业务流程：
    1. 检查是否有正在进行的对局（MVP 限制：同时只能 1 局）
    2. 混合模式必须提供 player_name
    3. 调用 GameService 创建对局 + 初始化 6 个玩家
    """
    service = GameService(db)

    # 检查是否有进行中的对局（MVP 限制同时只能 1 局）
    active = await service.get_active_game()
    if active:
        # HTTP 409 Conflict: 资源冲突
        raise HTTPException(status_code=409, detail="当前有对局进行中，请等待结束")

    # 混合模式必须提供玩家名称
    if request.mode == GameMode.MIXED and not request.player_name:
        # HTTP 400 Bad Request: 参数缺失
        raise HTTPException(status_code=400, detail="混合模式需要提供 player_name")

    game, owner_token, player_token = await service.create_game(request)
    roster = __import__("json").loads(game.roster_json)
    return ApiResponse(data={"game_id": game.id, "mode": game.mode, "status": game.status,
                              "owner_token": owner_token, "player_count": game.player_count,
                              "player_token": player_token,
                             "roster_type": game.roster_type, "roster": roster,
                             "validation": {"valid": True, "errors": []}})


# ─── 对局列表（分页查询） ─────────────────────────────────
# GET /api/v1/games?page=1&page_size=10&status=finished
@router.get("", response_model=ApiResponse)
async def list_games(
    page: int = Query(default=1, ge=1),                 # 页码，从 1 开始，ge=1 表示最小值为 1
    page_size: int = Query(default=10, ge=1, le=50),    # 每页条数，最大 50
    status: Optional[str] = Query(default=None),        # 可选过滤条件：按状态筛选
    db: AsyncSession = Depends(get_db),
):
    """对局列表（分页 + 可选状态过滤）

    返回格式: {"total": 100, "items": [GameSummary, ...]}
    排序：按创建时间倒序（最新的在前面）
    """
    # 基础查询：按创建时间倒序
    query = select(Game).order_by(Game.created_at.desc())
    # 如果有状态过滤条件，追加 WHERE 子句
    if status:
        query = query.where(Game.status == status)

    # 子查询计数：获取符合条件的总记录数（用于前端分页）
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # 分页查询：OFFSET + LIMIT
    items_q = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(items_q)
    games = result.scalars().all()

    # 将 ORM 对象转为 Pydantic Schema（自动序列化为 JSON 安全的字典）
    items = [
        GameSummary(
            game_id=g.id, mode=g.mode, status=g.status,
            winner=g.winner, total_rounds=g.total_rounds,
            created_at=g.created_at, finished_at=g.finished_at,
        ).model_dump()
        for g in games
    ]

    return ApiResponse(data={"total": total, "items": items})


# ─── 对局详情 ─────────────────────────────────────────────
# GET /api/v1/games/{game_id}
@router.get("/{game_id}", response_model=ApiResponse)
async def get_game(
    game_id: str,
    db: AsyncSession = Depends(get_db),
):
    """对局详情

    selectinload(Game.players) 的作用：
    - 在查询 Game 的同时，一次性加载关联的 GamePlayer 列表
    - 避免 N+1 查询问题（如果不用 eager loading，访问 game.players 会触发额外 SQL）

    角色可见性规则：
    - 游戏进行中（playing）：不返回角色信息（role=None），防止前端偷看
    - 游戏结束后（finished）：返回所有角色信息（role=具体角色）
    """
    result = await db.execute(
        select(Game)
        .options(selectinload(Game.players))    # 一次性加载玩家列表
        .where(Game.id == game_id)
    )
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="对局不存在")

    # 构建玩家列表：游戏未结束时隐藏角色信息
    players = [
        PlayerInfo(
            seat_number=p.seat_number,
            player_name=p.player_name,
            player_type=p.player_type,
            role=p.role if game.status == GameStatus.FINISHED else None,  # 核心：角色可见性控制
            is_alive=p.is_alive,
        ).model_dump()
        for p in game.players
    ]

    detail = GameDetail(
        game_id=game.id, mode=game.mode, status=game.status,
        current_round=game.total_rounds, winner=game.winner,
        end_reason=game.end_reason, players=players, player_count=game.player_count,
        roster_type=game.roster_type, roster=__import__("json").loads(game.roster_json),
        roster_locked=game.roster_locked_at is not None,
    )
    return ApiResponse(data=detail.model_dump())


@router.get("/{game_id}/events", response_model=ApiResponse)
async def get_public_events(game_id: str, db: AsyncSession = Depends(get_db)):
    """补偿 WS 连接前及断线期间错过的公开事件，绝不返回夜间私密载荷。"""
    events = (await db.execute(
        select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.created_at, GameEvent.id)
    )).scalars().all()
    if not events:
        game = (await db.execute(select(Game).where(Game.id == game_id))).scalar_one_or_none()
        if not game:
            raise HTTPException(status_code=404, detail="对局不存在")
    public_events = [payload for event in events if (payload := to_public_event(event)) is not None]
    return ApiResponse(data={"events": public_events})


# ─── 阵容配置（仅房主且仅 waiting） ────────────────────────
@router.patch("/{game_id}/roster", response_model=ApiResponse)
async def update_roster(game_id: str, request: RosterUpdateRequest, owner_token: str = Header(..., alias="X-Owner-Token"), db: AsyncSession = Depends(get_db)):
    service = GameService(db)
    game = await service.get_game_or_404(game_id)
    errors = await service.update_roster(game, owner_token, request.player_count, request.roster_type, request.roster)
    return ApiResponse(data={"player_count": game.player_count, "roster_type": game.roster_type,
                             "roster": request.roster, "validation": {"valid": not errors, "errors": errors}})


@router.post("/{game_id}/roster/reset-official", response_model=ApiResponse)
async def reset_official_roster(game_id: str, owner_token: str = Header(..., alias="X-Owner-Token"), db: AsyncSession = Depends(get_db)):
    service = GameService(db)
    game = await service.get_game_or_404(game_id)
    roster = await service.reset_official_roster(game, owner_token)
    return ApiResponse(data={"player_count": game.player_count, "roster": roster, "validation": {"valid": True, "errors": []}})


# ─── 开始对局 ─────────────────────────────────────────────
# POST /api/v1/games/{game_id}/start
@router.post("/{game_id}/start", response_model=ApiResponse)
async def start_game(
    game_id: str,
    owner_token: str = Header(..., alias="X-Owner-Token"),
    db: AsyncSession = Depends(get_db),
):
    """开始对局

    业务流程：
    1. 校验对局存在且状态为 waiting
    2. 将状态改为 playing
    3. 异步启动 LangGraph 游戏流程（asyncio.create_task，不阻塞 HTTP 响应）
    4. 立即返回 HTTP 响应，游戏在后台运行
    """
    service = GameService(db)
    game = await service.get_game_or_404(game_id)

    if game.status != GameStatus.WAITING:
        raise HTTPException(status_code=400, detail="对局状态不正确")

    # 启动游戏（内部用 asyncio.create_task 异步运行）
    await service.start_game(game, owner_token)
    return ApiResponse(data={"game_id": game.id, "status": "playing"})


# ─── 人类玩家身份验证（MVP 安全措施） ────────────────────

async def _verify_human_player(game_id: str, player_token: str, db: AsyncSession):
    """验证请求者是否为该对局的人类玩家

    MVP 安全措施：通过 X-Player-Seat header 验证请求者身份
    检查项：
    1. 对局存在且状态为 playing
    2. 对局模式为 mixed（纯 AI 模式不允许人类操作）
    3. player_seat 与对局中人类玩家的座位号一致

    不通过时抛出 403 Forbidden
    """
    result = await db.execute(
        select(Game)
        .options(selectinload(Game.players))
        .where(Game.id == game_id)
    )
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="对局不存在")
    if game.status != GameStatus.PLAYING:
        raise HTTPException(status_code=400, detail="对局不在进行中")
    if game.mode != GameMode.MIXED:
        raise HTTPException(status_code=403, detail="纯AI对局不允许人类操作")

    # 查找人类玩家的座位号
    human_player = next(
        (p for p in game.players if p.player_type == PlayerType.HUMAN), None
    )
    if not human_player:
        raise HTTPException(status_code=403, detail="该对局无人类玩家")
    if not player_token or not GameService._token_hash(player_token) == human_player.access_token_hash:
        raise HTTPException(status_code=403, detail="人类玩家凭据无效")
    return human_player


# ─── 夜晚行动（人类玩家） ──────────────────────────────────
# POST /api/v1/games/{game_id}/actions/night
@router.post("/{game_id}/actions/night", response_model=ApiResponse)
async def night_action(
    game_id: str,
    request: NightActionRequest,
    db: AsyncSession = Depends(get_db),
    player_token: str = Header(..., alias="X-Player-Token"),
):
    """夜晚行动（人类玩家提交）

    当轮到人类玩家在夜晚做决策时，前端调用此接口：
    - 狼人：选择击杀目标
    - 预言家：选择查验目标
    - 女巫：选择用解药/毒药/不用

    安全：通过 X-Player-Seat header 验证请求者身份
    """
    human_player = await _verify_human_player(game_id, player_token, db)
    service = GameService(db)
    await service.submit_night_action(game_id, human_player.seat_number, request)
    return ApiResponse(data={"accepted": True})


# ─── 提交发言（人类玩家） ──────────────────────────────────
# POST /api/v1/games/{game_id}/actions/speech
@router.post("/{game_id}/actions/speech", response_model=ApiResponse)
async def submit_speech(
    game_id: str,
    request: SpeechRequest,
    db: AsyncSession = Depends(get_db),
    player_token: str = Header(..., alias="X-Player-Token"),
):
    """提交发言（人类玩家在白天阶段公开发言）"""
    human_player = await _verify_human_player(game_id, player_token, db)
    service = GameService(db)
    await service.submit_speech(game_id, human_player.seat_number, request)
    return ApiResponse(data={"accepted": True})


# ─── 提交投票（人类玩家） ──────────────────────────────────
# POST /api/v1/games/{game_id}/actions/vote
@router.post("/{game_id}/actions/vote", response_model=ApiResponse)
async def submit_vote(
    game_id: str,
    request: VoteRequest,
    db: AsyncSession = Depends(get_db),
    player_token: str = Header(..., alias="X-Player-Token"),
):
    """提交投票（人类玩家在白天投票环节投票）"""
    human_player = await _verify_human_player(game_id, player_token, db)
    service = GameService(db)
    await service.submit_vote(game_id, human_player.seat_number, request)
    return ApiResponse(data={"accepted": True})
