"""AI 狼人杀 — 数据库会话管理

职责：
1. 创建异步数据库引擎（engine）
2. 创建会话工厂（async_session_factory）
3. 定义 ORM 基类（Base）
4. 提供 FastAPI 依赖注入函数（get_db）

架构关系：
  config.py → 提供 database_url
  session.py → 创建 engine + session
  models/*.py → 继承 Base 定义表结构
  main.py → 启动时调用 Base.metadata.create_all 自动建表
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# ─── 创建异步数据库引擎 ────────────────────────────────────
# engine 是 SQLAlchemy 的核心，负责：
# - 管理数据库连接池
# - 执行 SQL 语句
# - 处理数据库方言差异
#
# echo=False: 不打印 SQL 日志（生产环境），调试时可设为 True
# connect_args: SQLite 特殊参数，check_same_thread=False 允许跨线程使用同一连接
#   （因为 asyncio 的事件循环可能在不同线程调度）
engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

# ─── 创建异步会话工厂 ──────────────────────────────────────
# async_sessionmaker 是一个工厂类，每次调用生成一个新的 AsyncSession
# AsyncSession 是一次数据库会话（事务），用完需要关闭
#
# class_=AsyncSession: 指定会话类型为异步版本
# expire_on_commit=False: commit 后不自动过期对象属性
#   （默认 True 会导致 commit 后访问对象属性触发延迟加载，在 async 模式下会报错）
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ─── ORM 基类 ─────────────────────────────────────────────
# 所有数据模型（Game、GamePlayer 等）都继承这个 Base
# Base.metadata 会收集所有子类的表定义信息
# main.py 启动时调用 Base.metadata.create_all 来自动建表
class Base(DeclarativeBase):
    """SQLAlchemy ORM 基类

    DeclarativeBase 是 SQLAlchemy 2.0 推荐的基类写法
    所有继承它的子类会自动注册到 Base.metadata 中
    """
    pass


# ─── FastAPI 依赖注入 ──────────────────────────────────────
# 在 API 路由中通过 Depends(get_db) 注入数据库会话
#
# 工作流程：
# 1. 请求进入 → async_session_factory() 创建新 session
# 2. yield session → 路由函数拿到 session 执行业务逻辑
# 3. 路由函数正常返回 → await session.commit() 提交事务
# 4. 路由函数抛出异常 → await session.rollback() 回滚事务
# 5. async with 退出 → session 自动关闭，连接归还连接池
async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话

    使用方式：
        async def my_api(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Game))
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()      # 正常完成 → 提交事务
        except Exception:
            await session.rollback()    # 异常发生 → 回滚事务
            raise                       # 重新抛出异常，让 FastAPI 处理
