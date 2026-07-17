"""2.0 的纯业务规则：可独立测试，不依赖数据库或 LangGraph。"""

from typing import Mapping

from app.models.game import PlayerRole

ROLE_KEYS = (
    PlayerRole.WEREWOLF, PlayerRole.VILLAGER, PlayerRole.SEER,
    PlayerRole.WITCH, PlayerRole.HUNTER, PlayerRole.GUARD,
)
OFFICIAL_ROSTERS = {
    6: {"werewolf": 2, "villager": 2, "seer": 1, "witch": 1, "hunter": 0, "guard": 0},
    12: {"werewolf": 4, "villager": 4, "seer": 1, "witch": 1, "hunter": 1, "guard": 1},
}


def validate_roster(player_count: int, roster_type: str, roster: Mapping[str, int]) -> list[str]:
    """返回全部阵容错误；非法草稿可保存，但不能用于开局。"""
    errors: list[str] = []
    if player_count not in OFFICIAL_ROSTERS:
        errors.append("对局人数仅支持 6 或 12")
    if set(roster) != set(ROLE_KEYS):
        errors.append("阵容必须包含且仅包含六种本期角色")
        return errors
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in roster.values()):
        errors.append("角色数量必须为非负整数")
    if sum(roster.values()) != player_count:
        errors.append(f"当前阵容共 {sum(roster.values())} 人，应为 {player_count} 人")
    if player_count in OFFICIAL_ROSTERS:
        expected_wolves = OFFICIAL_ROSTERS[player_count][PlayerRole.WEREWOLF]
        if roster[PlayerRole.WEREWOLF] != expected_wolves:
            errors.append(f"{player_count} 人对局必须配置 {expected_wolves} 名狼人")
    for role in (PlayerRole.SEER, PlayerRole.WITCH, PlayerRole.HUNTER, PlayerRole.GUARD):
        if roster[role] > 1:
            errors.append(f"{_role_name(role)}最多只能配置 1 名")
    if roster_type not in ("official", "custom"):
        errors.append("阵容类型仅支持 official 或 custom")
    elif roster_type == "official" and player_count in OFFICIAL_ROSTERS and dict(roster) != OFFICIAL_ROSTERS[player_count]:
        errors.append("官方阵容必须与该人数的默认阵容完全一致")
    return errors


def guard_target_is_valid(target: int, last_target: int | None, alive_seats: list[int]) -> tuple[bool, str]:
    if target not in alive_seats:
        return False, "守护目标必须存活"
    if target == last_target:
        return False, "守卫不能连续两夜守护同一目标"
    return True, ""


def resolve_night_deaths(
    kill_target: int | None, witch_action: str, witch_target: int | None, guard_target: int | None,
) -> dict[int, str]:
    """按 2.0 优先级得出每个死者的主死因，毒药优先且死亡集合去重。"""
    deaths: dict[int, str] = {}
    if kill_target is not None and guard_target != kill_target and witch_action != "save":
        deaths[kill_target] = "killed_by_werewolf"
    if witch_action == "poison" and witch_target is not None:
        deaths[witch_target] = "poisoned"
    return deaths


def can_hunter_shoot(death_reason: str | None) -> bool:
    return death_reason in {"killed_by_werewolf", "voted_out"}


def _role_name(role: str) -> str:
    return {"seer": "预言家", "witch": "女巫", "hunter": "猎人", "guard": "守卫"}[role]
