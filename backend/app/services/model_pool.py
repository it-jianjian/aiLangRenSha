"""用户自定义模型池 — 运行时缓存 + DB(custom_models) 持久化。

前端可自行添加模型（name/api_key/base_url/temperature），存库后即刻可选可用，
无需修改服务器 .env 或重启。create_llm 通过 get_provider(model_name) 取 provider。
"""

from typing import Optional

_POOL: dict[str, dict] = {}


def set_pool(entries: list[dict]) -> None:
    """以列表整体替换缓存池。entries: [{name, api_key, base_url, temperature}]"""
    global _POOL
    _POOL = {
        e["name"]: {
            "api_key": e["api_key"],
            "base_url": e["base_url"],
            "temperature": e.get("temperature"),
        }
        for e in entries
        if e.get("name")
    }


def get_pool() -> dict[str, dict]:
    return {k: dict(v) for k, v in _POOL.items()}


def get_provider(model_name: Optional[str]) -> Optional[dict]:
    if not model_name:
        return None
    return _POOL.get(model_name)


def upsert(name: str, api_key: str, base_url: str, temperature: Optional[float] = None) -> None:
    _POOL[name] = {"api_key": api_key, "base_url": base_url, "temperature": temperature}


def remove(name: str) -> None:
    _POOL.pop(name, None)


async def load_from_db() -> None:
    """启动时从 custom_models 表载入模型池到缓存。"""
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.game import CustomModel

    async with async_session_factory() as session:
        rows = (await session.execute(select(CustomModel))).scalars().all()
    set_pool([
        {"name": r.name, "api_key": r.api_key, "base_url": r.base_url, "temperature": r.temperature}
        for r in rows
    ])
