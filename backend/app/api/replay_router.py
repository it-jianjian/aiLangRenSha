"""AI 狼人杀 — 回放 API 路由

职责：提供对局回放数据查询接口
路由前缀：/api/v1/games（在 main.py 中注册）

包含的接口：
  GET /api/v1/games/{id}/replay — 获取对局回放数据

回放功能的数据来源：
  GameEvent 表记录了游戏中的每一个事件，按时间排序就是完整的游戏时间线
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.game_schemas import ApiResponse, ReplayData, ReplayStep
from app.db.session import get_db
from app.models.game import Game, GameEvent, GamePlayer

router = APIRouter()


# ─── 获取回放数据 ─────────────────────────────────────────
# GET /api/v1/games/{game_id}/replay
@router.get("/{game_id}/replay", response_model=ApiResponse)
async def get_replay(
    game_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取对局回放数据

    前置条件：对局必须已结束（status=finished），进行中的对局不允许回放

    返回数据结构：
    {
        "game_id": "xxx",
        "total_steps": 50,
        "steps": [ReplayStep, ...],     // 按时间排序的事件列表
        "role_mapping": {"1": "werewolf", "2": "villager", ...}  // 所有玩家角色
    }
    """
    # ─── 查询对局（含玩家信息） ───
    result = await db.execute(
        select(Game)
        .options(selectinload(Game.players))    # 一次性加载玩家列表
        .where(Game.id == game_id)
    )
    game = result.scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="对局不存在")
    if game.status != "finished":
        raise HTTPException(status_code=400, detail="对局尚未结束，无法回放")

    # ─── 查询所有事件（按时间和 ID 稳定排序） ───
    events_result = await db.execute(
        select(GameEvent)
        .where(GameEvent.game_id == game_id)
        .order_by(GameEvent.created_at, GameEvent.id)         # (created_at, id) 稳定排序
    )
    events = events_result.scalars().all()

    # ─── 构建角色映射（座位号 → 角色） ───
    # 回放时需要揭示所有玩家身份，所以这里返回完整映射
    role_mapping = {str(p.seat_number): p.role for p in game.players}

    # ─── 将事件转为回放步骤 ───
    steps = []
    for i, event in enumerate(events):
        steps.append(ReplayStep(
            step_index=i,                       # 步骤序号
            round=event.round_number,            # 所属回合号
            phase=event.phase,                  # night/day/system
            event_type=event.event_type,        # 事件类型
            description=_event_description(event),  # 人类可读描述
            event_data=_parse_json(event.event_data),  # 事件详细数据
        ))

    replay = ReplayData(
        game_id=game.id,
        total_steps=len(steps),
        steps=steps,
        role_mapping=role_mapping,
        player_count=game.player_count,
        roster=_parse_json(game.roster_json) or {},
        winner=game.winner,
        end_reason=game.end_reason,
    )
    return ApiResponse(data=replay.model_dump())


# ─── 辅助函数 ─────────────────────────────────────────────

def _event_description(event: GameEvent) -> str:
    """根据事件类型生成人类可读的描述文本

    优先从 GameEvent.seat_number 读取执行者座位，避免从 event_data 中错误取值。
    """
    import json
    data = json.loads(event.event_data) if event.event_data else {}
    seat = event.seat_number if event.seat_number is not None else "?"
    target = data.get("target", "?")
    desc_map = {
        "role_assign": "角色分配完成",
        "night_kill": "狼人选择了击杀目标",
        "night_verify": "预言家进行了查验",
        "night_save": "女巫使用了解药",
        "night_poison": "女巫使用了毒药",
        "night_settle": "夜晚结算完成",
        "night_guard": f"守卫守护了 {target} 号",
        "hunter_revenge": "猎人放弃开枪",
        "hunter_shot": f"猎人开枪带走了 {target} 号",
        "death_announce": "公布死亡信息",
        "last_words": f"{seat} 号玩家发表遗言",
        "speech": f"{seat} 号玩家发言",
        "vote": f"{seat} 号玩家投票",
        "vote_result": "投票结果公布",
        "eliminate": f"{seat} 号玩家被淘汰",
        "victory_check": "胜负检查",
        "game_over": f"游戏结束 - {data.get('winner', '?')} 阵营胜利",
    }
    return desc_map.get(event.event_type, event.event_type)


def _parse_json(text: str | None) -> dict | None:
    """安全解析 JSON 字符串，失败返回 None"""
    if not text:
        return None
    import json
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
