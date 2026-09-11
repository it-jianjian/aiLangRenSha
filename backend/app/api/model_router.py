"""自定义模型池 API — 用户自加模型名/API Key/Base URL，存库免重启。

路由前缀：/api/v1/models（在 main.py 注册）
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.game_schemas import ApiResponse
from app.db.session import get_db
from app.models.game import CustomModel
from app.services import model_pool

router = APIRouter()


class CustomModelRequest(BaseModel):
    name: str
    api_key: str
    base_url: str
    temperature: Optional[float] = None


@router.get("", response_model=ApiResponse)
async def list_pool():
    """返回自定义模型池（api_key 掩码）。"""
    pool = model_pool.get_pool()
    items = [
        {
            "name": k,
            "base_url": v["base_url"],
            "temperature": v["temperature"],
            "api_key_masked": (v["api_key"][:6] + "***") if v["api_key"] else "",
        }
        for k, v in pool.items()
    ]
    return ApiResponse(data={"items": items})


@router.post("", response_model=ApiResponse)
async def upsert_model(request: CustomModelRequest, db: AsyncSession = Depends(get_db)):
    """新增/更新自定义模型；写入 DB 并刷新运行时缓存。"""
    name = request.name.strip()
    if not name or not request.api_key or not request.base_url:
        raise HTTPException(status_code=400, detail="name/api_key/base_url 必填")
    existing = (await db.execute(select(CustomModel).where(CustomModel.name == name))).scalar_one_or_none()
    if existing:
        existing.api_key = request.api_key
        existing.base_url = request.base_url.rstrip("/")
        existing.temperature = request.temperature
    else:
        db.add(CustomModel(name=name, api_key=request.api_key,
                           base_url=request.base_url.rstrip("/"), temperature=request.temperature))
    await db.commit()
    await model_pool.load_from_db()
    return ApiResponse(data={"ok": True})


@router.delete("/{name}", response_model=ApiResponse)
async def delete_model(name: str, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(CustomModel).where(CustomModel.name == name))).scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="模型不存在")
    await db.delete(existing)
    await db.commit()
    await model_pool.load_from_db()
    return ApiResponse(data={"ok": True})
