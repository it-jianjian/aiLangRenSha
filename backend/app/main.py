"""AI 狼人杀 — 后端应用入口

职责：
1. 创建 FastAPI 应用实例
2. 配置中间件（CORS 跨域）
3. 注册所有 API 路由 + WebSocket 端点
4. 管理应用生命周期（启动时自动建表）

启动命令: uvicorn app.main:app --host 0.0.0.0 --port 8000
--ws-ping-interval 20 --ws-ping-timeout 20
（ws-ping 为协议层心跳，配合应用层 {"type": "ping"} 心跳，
防止云服务器反向代理/负载均衡因空闲超时断开 WebSocket）
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器

    FastAPI 的 lifespan 是一个异步上下文管理器：
    - yield 之前的代码在应用**启动时**执行（类似 Java 的 @PostConstruct）
    - yield 之后的代码在应用**关闭时**执行（类似 Java 的 @PreDestroy）

    这里做的事情：
    1. 确保 data/ 目录存在（SQLite 文件存放位置）
    2. 自动创建所有 ORM 模型对应的数据库表（Base.metadata.create_all）
       - 如果表已存在则跳过，不会重复创建
       - 这是 MVP 阶段的简化方案，生产环境应该用 Alembic 做数据库迁移
    """
    settings = get_settings()
    settings.data_dir  # 触发目录创建（data/ 不存在时自动 mkdir）

    # engine.begin() 开启一个数据库事务
    # run_sync() 把同步的 create_all 包装成异步执行
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # create_all 只负责空库建表；历史库字段升级由 Alembic 负责，失败则不接受流量。
    from app.db.migrations import upgrade_database
    await asyncio.to_thread(upgrade_database, settings.database_url)

    # 载入用户自定义模型池（存库免重启）
    from app.services import model_pool
    await model_pool.load_from_db()

    # R1: 尝试恢复进行中的对局（有检查点则续跑，无检查点则终止）
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.game import Game, GameStatus
    async with async_session_factory() as session:
        result = await session.execute(select(Game).where(Game.status == GameStatus.PLAYING))
        playing_games = result.scalars().all()
    if playing_games:
        import logging
        log = logging.getLogger(__name__)
        from app.db.checkpoint import get_checkpointer
        for game in playing_games:
            config = {"configurable": {"thread_id": game.id}}
            async with get_checkpointer() as checkpointer:
                saved = await checkpointer.aget(config)
            if saved:
                log.info(f"启动时恢复对局 {game.id[:8]}...（有检查点，续跑）")
                from app.graphs.game_flow import resume_game
                asyncio.create_task(resume_game(game.id))
            else:
                log.warning(f"启动时终止对局 {game.id[:8]}...（无检查点，无法恢复）")
                async with async_session_factory() as session:
                    g = await session.get(Game, game.id)
                    if g:
                        g.status = GameStatus.FINISHED
                        g.end_reason = "interrupted_by_restart"
                        from datetime import datetime
                        g.finished_at = datetime.now()
                await session.commit()

    # 启动 WebSocket 应用层心跳：周期性 ping 所有连接，顶住代理空闲超时并提前清理死连接
    from app.api.ws_handler import ws_manager
    ws_manager.start_heartbeat()

    yield  # ← 应用在此处开始接受请求，关闭时继续执行下方

    await ws_manager.stop_heartbeat()

    # B4: 应用关闭时刷写剩余 AgentLog
    from app.graphs.agent_graph import _flush_log_queue
    _flush_log_queue()


def create_app() -> FastAPI:
    """工厂函数：创建并配置 FastAPI 应用

    使用工厂函数而非直接在模块级创建 app，好处：
    - 可以在测试中传入不同配置创建独立实例
    - 代码结构更清晰，所有配置集中在一处
    """
    settings = get_settings()

    app = FastAPI(
        title="AI 狼人杀",
        description="基于 LangChain + LangGraph 的多 Agent 狼人杀对战平台",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ─── CORS 中间件配置 ─────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── 注册 API 路由 ──────────────────────────────────────
    from app.api.game_router import router as game_router
    from app.api.replay_router import router as replay_router
    from app.api.review_router import router as review_router
    from app.api.model_router import router as model_router
    from app.api.auth_router import router as auth_router

    app.include_router(game_router, prefix="/api/v1/games", tags=["对局"])
    app.include_router(replay_router, prefix="/api/v1/games", tags=["回放"])
    app.include_router(review_router, prefix="/api/v1/games", tags=["复盘"])
    app.include_router(model_router, prefix="/api/v1/models", tags=["模型池"])
    app.include_router(auth_router, prefix="/api/v1/auth", tags=["账号"])

    # ─── WebSocket 端点 ──────────────────────────────────────
    from app.api.ws_handler import ws_manager

    @app.websocket("/ws/game/{game_id}")
    async def websocket_endpoint(websocket: WebSocket, game_id: str):
        """WebSocket 端点：前端通过此连接接收游戏实时事件

        连接地址: ws://host/ws/game/{game_id}
        消息格式: {"type": "事件类型", "data": {...}, "timestamp": "ISO8601"}
        """
        # 所有连接先作为匿名观察者接入以接收公共事件。只有首帧认证成功后
        # 才会绑定座位并投递私密身份；令牌绝不出现在 URL、日志或错误消息中。
        #
        # 关键修复：等待态（waiting）也允许绑定座位。前端在等待房间加载时即连
        # 接 WS 并认证，若此时拒绝（旧逻辑要求 status==playing），连接会被
        # 1008 关闭且前端不重连，导致开局后所有实时推送收不到、必须手动刷新。
        # 等待态绑定后，对局启动时 _send_game_started 会向已绑定连接投递身份。
        await ws_manager.connect(websocket, game_id)
        authenticated = False
        try:
            while True:
                message_text = await websocket.receive_text()
                if authenticated:
                    continue

                from app.api.ws_handler import extract_authentication_token, resolve_player_binding
                from app.db.session import async_session_factory
                player_token = extract_authentication_token(message_text)
                if not player_token:
                    await websocket.send_json({"type": "authentication_failed", "data": {"code": "invalid_authentication"}})
                    await websocket.close(code=1008)
                    return

                async with async_session_factory() as session:
                    binding = await resolve_player_binding(session, game_id, player_token)
                if not binding:
                    await websocket.send_json({"type": "authentication_failed", "data": {"code": "invalid_authentication"}})
                    await websocket.close(code=1008)
                    return

                ws_manager.bind_player(websocket, game_id, binding["seat_number"])
                authenticated = True
                # 仅在对局进行中才立即投递身份；等待态绑定后由 _send_game_started 在开局时投递。
                if binding["identity_sync_allowed"]:
                    await ws_manager.send_to_seat(game_id, binding["seat_number"], {
                        "type": "identity_sync",
                        "data": {
                            "seat": binding["seat_number"],
                            "role": binding["role"],
                            "werewolf_companions": binding["companions"],
                        },
                    })
        except WebSocketDisconnect:
            pass
        finally:
            ws_manager.disconnect(websocket, game_id)

    # ─── 健康检查接口 ────────────────────────────────────────
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app


# ─── 应用实例（uvicorn 会找这个变量） ──────────────────────
app = create_app()
