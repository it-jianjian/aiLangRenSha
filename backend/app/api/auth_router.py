"""可选登录 API — 注册/登录/个人资料/我的对局/战绩。

路由前缀：/api/v1/auth（在 main.py 注册）。登录为可选：不登录仍可建局/玩/观战。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.game_schemas import ApiResponse
from app.db.session import get_db
from app.models.game import Game, GamePlayer, User
from app.services import auth_service

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None
    email: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class UpdateMeRequest(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    email: Optional[str] = None


def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "nickname": u.nickname,
        "avatar": u.avatar, "bio": u.bio, "email": u.email,
    }


async def _current_user_id(authorization: Optional[str] = Header(None, alias="Authorization")) -> Optional[str]:
    if not authorization:
        return None
    return auth_service.verify_token(authorization.removeprefix("Bearer ").strip() or None)


@router.post("/register", response_model=ApiResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    username = req.username.strip()
    if not username or len(req.password) < 6:
        raise HTTPException(status_code=400, detail="用户名必填、密码至少 6 位")
    exists = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=username,
        password_hash=auth_service.hash_password(req.password),
        nickname=req.nickname or username,
        email=req.email,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return ApiResponse(data={"token": auth_service.create_token(user.id), "user": _user_dict(user)})


@router.post("/login", response_model=ApiResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.username == req.username.strip()))).scalar_one_or_none()
    if not user or not auth_service.verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return ApiResponse(data={"token": auth_service.create_token(user.id), "user": _user_dict(user)})


@router.get("/me", response_model=ApiResponse)
async def me(db: AsyncSession = Depends(get_db), uid: Optional[str] = Depends(_current_user_id)):
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return ApiResponse(data={"user": _user_dict(user)})


@router.put("/me", response_model=ApiResponse)
async def update_me(
    req: UpdateMeRequest, db: AsyncSession = Depends(get_db), uid: Optional[str] = Depends(_current_user_id),
):
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.nickname is not None:
        user.nickname = req.nickname
    if req.avatar is not None:
        user.avatar = req.avatar
    if req.bio is not None:
        user.bio = req.bio
    if req.email is not None:
        user.email = req.email
    await db.commit()
    return ApiResponse(data={"user": _user_dict(user)})


@router.get("/me/games", response_model=ApiResponse)
async def my_games(db: AsyncSession = Depends(get_db), uid: Optional[str] = Depends(_current_user_id)):
    """我创建的 或 我作为人类玩家参与的 对局列表。"""
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    owned = (await db.execute(select(Game).where(Game.owner_user_id == uid))).scalars().all()
    participated = (await db.execute(
        select(Game).join(GamePlayer, GamePlayer.game_id == Game.id).where(GamePlayer.user_id == uid)
    )).scalars().all()
    seen: dict[str, Game] = {}
    for g in list(owned) + list(participated):
        seen[g.id] = g
    items = [
        {
            "game_id": g.id, "mode": g.mode, "status": g.status, "winner": g.winner,
            "total_rounds": g.total_rounds,
            "created_at": g.created_at.isoformat() if g.created_at else None,
        }
        for g in sorted(seen.values(), key=lambda x: x.created_at or "", reverse=True)
    ]
    return ApiResponse(data={"items": items})


@router.get("/me/stats", response_model=ApiResponse)
async def my_stats(db: AsyncSession = Depends(get_db), uid: Optional[str] = Depends(_current_user_id)):
    """战绩：我作为人类玩家参与的已结束对局，按阵营侧统计胜负 + 我创建的局数。"""
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    rows = (await db.execute(
        select(GamePlayer.role, Game.winner)
        .join(Game, Game.id == GamePlayer.game_id)
        .where(GamePlayer.user_id == uid, Game.status == "finished")
    )).all()
    stats = {
        "games": 0, "wins": 0, "losses": 0,
        "as_werewolf": {"games": 0, "wins": 0},
        "as_good": {"games": 0, "wins": 0},
    }
    for role, winner in rows:
        stats["games"] += 1
        side = "as_werewolf" if role == "werewolf" else "as_good"
        stats[side]["games"] += 1
        won = (winner == "werewolf") if role == "werewolf" else (winner not in (None, "werewolf"))
        if won:
            stats["wins"] += 1
            stats[side]["wins"] += 1
        else:
            stats["losses"] += 1
    created = (await db.execute(select(func.count(Game.id)).where(Game.owner_user_id == uid))).scalar() or 0
    stats["games_created"] = created
    return ApiResponse(data=stats)
