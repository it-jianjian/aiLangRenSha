"""AI 狼人杀 — 复盘 API 路由

职责：提供对局复盘（胜率曲线 + AI 点评）查询与生成接口
路由前缀：/api/v1/games（在 main.py 注册）

包含的接口：
  GET  /api/v1/games/{id}/review           — 胜率曲线（实时规则计算）+ 已缓存 AI 点评
  POST /api/v1/games/{id}/review/generate  — 生成/重新生成 AI 复盘点评（缓存进 game_reviews）

数据来源：
  胜率曲线由 win_probability 规则引擎实时计算（基于 GamePlayer 存活重建），永远可用；
  AI 点评由 game_review 调 LLM 生成并缓存，未生成/降级时 insight=null，不阻塞曲线。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.game_schemas import ApiResponse, ReviewData
from app.db.session import get_db
from app.services.game_review import build_review_data, generate_review

router = APIRouter()


@router.get("/{game_id}/review", response_model=ApiResponse)
async def get_review(
    game_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取复盘数据：胜率曲线（实时）+ 已缓存的 AI 点评（无则 insight=null）。

    前置条件：对局必须已结束（status=finished），否则 400。
    """
    try:
        data = await build_review_data(db, game_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="对局尚未结束，无法复盘")
    if data is None:
        raise HTTPException(status_code=404, detail="对局不存在")
    review = ReviewData(**data)
    return ApiResponse(data=review.model_dump())


@router.post("/{game_id}/review/generate", response_model=ApiResponse)
async def generate_game_review(
    game_id: str,
    db: AsyncSession = Depends(get_db),
):
    """生成（或重新生成）AI 复盘点评，结果缓存进 game_reviews。

    前置条件：对局必须已结束，否则 400。
    LLM 不可用/解析失败时降级为 insight=null（胜率曲线仍照常返回）。
    """
    try:
        data = await generate_review(db, game_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="对局尚未结束，无法复盘")
    if data is None:
        raise HTTPException(status_code=404, detail="对局不存在")
    review = ReviewData(**data)
    return ApiResponse(data=review.model_dump())
