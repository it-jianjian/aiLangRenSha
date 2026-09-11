"""按局存储「座位 → 模型」覆盖映射（前端弹窗配置）。

- 运行时注册表 _REG: {game_id: {seat: model_name}}，供 create_llm 查询。
- 持久化在 Game.config_json["seat_models"]，服务重启/续跑时由 run_game 重新载入。
- 优先级：显式 model_name > 本注册表(按局按座位) > action_type 路由 > 配置座位实例 > 默认。
"""

from typing import Optional

_REG: dict[str, dict[int, str]] = {}


def set_seat_models(game_id: str, mapping: dict[int, str]) -> None:
    """设置某局各座位的模型覆盖（空值座位会被忽略）。"""
    clean = {int(seat): model for seat, model in (mapping or {}).items() if model}
    _REG[game_id] = clean


def get_seat_model(game_id: Optional[str], seat_number: Optional[int]) -> Optional[str]:
    """查询某局某座位的模型覆盖；无则返回 None。"""
    if not game_id or seat_number is None:
        return None
    return (_REG.get(game_id) or {}).get(int(seat_number))


def get_seat_models(game_id: str) -> dict[int, str]:
    return dict(_REG.get(game_id) or {})


def clear_seat_models(game_id: str) -> None:
    _REG.pop(game_id, None)


def load_from_config_json(game_id: str, config_json: Optional[str]) -> None:
    """从 Game.config_json 载入 seat_models 到注册表（续跑/重启用）。"""
    import json

    if not config_json:
        return
    try:
        cfg = json.loads(config_json)
    except (json.JSONDecodeError, TypeError):
        return
    sm = cfg.get("seat_models") if isinstance(cfg, dict) else None
    if isinstance(sm, dict):
        set_seat_models(game_id, {int(k): v for k, v in sm.items() if isinstance(v, str) and v})
