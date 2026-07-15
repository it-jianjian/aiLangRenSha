"""AI 狼人杀 — 后端应用入口

职责：
1. 创建 FastAPI 应用实例
2. 配置中间件（CORS 跨域）
3. 注册所有 API 路由 + WebSocket 端点
4. 管理应用生命周期（启动时自动建表）

启动命令: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import engine, Base


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

    yield  # ← 应用在此处开始接受请求，关闭时继续执行下方


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

    app.include_router(game_router, prefix="/api/v1/games", tags=["对局"])
    app.include_router(replay_router, prefix="/api/v1/games", tags=["回放"])

    # ─── WebSocket 端点 ──────────────────────────────────────
    from app.api.ws_handler import ws_manager

    @app.websocket("/ws/game/{game_id}")
    async def websocket_endpoint(websocket: WebSocket, game_id: str):
        """WebSocket 端点：前端通过此连接接收游戏实时事件

        连接地址: ws://host/ws/game/{game_id}
        消息格式: {"type": "事件类型", "data": {...}, "timestamp": "ISO8601"}
        """
        await ws_manager.connect(websocket, game_id)
        try:
            while True:
                # 保持连接，等待客户端消息（如人类玩家操作指令）
                await websocket.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket, game_id)

    # ─── 健康检查接口 ────────────────────────────────────────
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    return app


# ─── 应用实例（uvicorn 会找这个变量） ──────────────────────
app = create_app()
