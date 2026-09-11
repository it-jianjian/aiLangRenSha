"""AI 狼人杀 — 胜率引擎（复盘功能：胜率曲线核心算法）

纯业务函数模块，不依赖数据库或 LangGraph，可独立单元测试（风格对齐 game_rules.py）。

上帝视角「好人胜率」定义：
- 基于每个关键节点（夜晚结算后 / 白天淘汰后）的存活阵营力量对比计算；
- 与 victory_check.py 的终局条件严格对齐收敛：
    * 存活狼人 = 0            → 好人必胜（1.0）
    * 存活狼人 ≥ 存活好人      → 好人必败（0.0）
- 中间态用「有效好人力 / (有效好人力 + 狼人力)」插值，神职按权重加成，
  数值确定、可复现、无需 LLM。

调用链：review_router / game_review → build_win_curve(players, ...) → compute_good_win_prob
"""

from typing import Any, Iterable, Optional

# ─── 权重常量（集中调参） ─────────────────────────────────
# 好人各角色存活时的“力量权重”：神职高于平民（信息 / 技能价值）
GOOD_ROLE_WEIGHT: dict[str, float] = {
    "villager": 1.0,
    "seer": 1.35,     # 预言家：查验信息，价值最高
    "witch": 1.25,    # 女巫：一救一毒
    "hunter": 1.15,   # 猎人：死亡反杀
    "guard": 1.05,    # 守卫：守护
}
# 狼人方整体强度系数（夜刀 + 互通身份的信息优势）
WOLF_FACTOR: float = 1.2

# ─── 节点类型 ─────────────────────────────────────────────
CHECKPOINT_START = "start"                # 开局（全员存活）
CHECKPOINT_AFTER_NIGHT = "after_night"    # 每轮夜晚结算后
CHECKPOINT_AFTER_DAY = "after_day"        # 每轮白天淘汰后
CHECKPOINT_FINAL = "final"                # 终局（由 winner 收敛）


def _role_key(role: Any) -> str:
    """兼容枚举与字符串：PlayerRole.WEREWOLF / "werewolf" → "werewolf"。"""
    return getattr(role, "value", role)


def compute_good_win_prob(alive_roles: Iterable[Any]) -> float:
    """给定当前存活玩家的角色列表，返回上帝视角「好人胜率」(0.0~1.0)。

    终局对齐（与 victory_check._count_alive_by_faction 一致）：
      - 无存活狼人 → 1.0
      - 存活狼人 ≥ 存活好人 → 0.0
    中间态：effective_good / (effective_good + wolves * WOLF_FACTOR)
      其中 effective_good = 好人数 + Σ(存活神职权重 - 1) 加成。
    """
    roles = [_role_key(r) for r in alive_roles]
    wolves = sum(1 for r in roles if r == "werewolf")
    goods = sum(1 for r in roles if r != "werewolf")

    # ─── 终局收敛 ───
    if wolves == 0:
        return 1.0
    if wolves >= goods:
        return 0.0

    # ─── 中间态：神职加权插值 ───
    bonus = sum(GOOD_ROLE_WEIGHT.get(r, 1.0) - 1.0 for r in roles if r != "werewolf")
    effective_good = goods + bonus
    denom = effective_good + wolves * WOLF_FACTOR
    if denom <= 0:
        return 0.0
    p = effective_good / denom
    return max(0.0, min(1.0, p))


def _death_key(player: dict) -> Optional[tuple[int, int]]:
    """玩家的死亡时间键 (round, phase_order)；仍存活返回 None。

    phase_order：0 = 夜晚结算死亡，1 = 白天死亡。用于在时间轴上与节点键比较。
    """
    if player.get("is_alive", True):
        return None
    dr = player.get("death_round")
    if dr is None:
        # 死亡但缺轮次信息（数据异常）：归到最早时刻，避免曲线错位
        return (0, 0)
    phase = player.get("death_phase") or "night"
    return (int(dr), 0 if phase == "night" else 1)


def _is_alive_at(player: dict, node_key: tuple[int, int]) -> bool:
    """玩家在给定节点时间键是否仍存活：死亡键 > 节点键 → 尚未死亡。"""
    dk = _death_key(player)
    if dk is None:
        return True
    return dk > node_key


def _default_label(checkpoint: str, deaths: list[int]) -> str:
    """根据节点类型与死亡名单生成人类可读标签。"""
    names = "、".join(f"{s}号" for s in deaths)
    if checkpoint == CHECKPOINT_AFTER_NIGHT:
        return f"昨夜 {names} 遇难" if deaths else "平安夜"
    if checkpoint == CHECKPOINT_AFTER_DAY:
        return f"{names} 被放逐" if deaths else "平安日"
    return ""


def build_win_curve(
    players: list[dict],
    total_rounds: int,
    winner: Optional[str] = None,
) -> list[dict]:
    """构建整局胜率曲线（上帝视角好人胜率随关键节点变化）。

    节点序列：start → 每轮 after_night(r) / after_day(r) → final。

    参数:
        players: 每项需含 seat_number, role, is_alive, death_round, death_phase
        total_rounds: 已完成轮数（决定 after_night/after_day 节点数量）
        winner: "villager" / "werewolf" / None；非空时追加 final 收敛点

    返回:
        list[dict]，每个 dict 为一个 WinPoint（字段见方案）。
    """
    curve: list[dict] = []
    step = 0

    def emit(node_key: tuple[int, int], rnd: int, checkpoint: str, label: Optional[str] = None) -> None:
        nonlocal step
        alive_roles: list[str] = []
        wolves = goods = 0
        deaths: list[int] = []
        for p in players:
            dk = _death_key(p)
            if dk is not None and dk == node_key and p.get("seat_number") is not None:
                deaths.append(p["seat_number"])
            if _is_alive_at(p, node_key):
                rk = _role_key(p.get("role"))
                alive_roles.append(rk)
                if rk == "werewolf":
                    wolves += 1
                else:
                    goods += 1
        deaths.sort()
        prob = compute_good_win_prob(alive_roles)
        curve.append({
            "step": step,
            "round": rnd,
            "checkpoint": checkpoint,
            "good_win_prob": round(prob, 4),
            "alive_wolves": wolves,
            "alive_goods": goods,
            "event_label": label if label is not None else _default_label(checkpoint, deaths),
            "deaths": deaths,
        })
        step += 1

    # ─── 开局：全员存活（节点键置于第 1 轮夜晚之前） ───
    emit((1, -1), 0, CHECKPOINT_START, label="开局")

    # ─── 每轮夜晚结算后 / 白天淘汰后 ───
    for r in range(1, max(int(total_rounds or 0), 0) + 1):
        emit((r, 0), r, CHECKPOINT_AFTER_NIGHT)
        emit((r, 1), r, CHECKPOINT_AFTER_DAY)

    # ─── 终局：由 winner 收敛到 0/1 ───
    if winner:
        alive_roles = [_role_key(p.get("role")) for p in players if p.get("is_alive", True)]
        wolves = sum(1 for x in alive_roles if x == "werewolf")
        goods = sum(1 for x in alive_roles if x != "werewolf")
        final_prob = 1.0 if winner == "villager" else 0.0
        label = "好人胜利" if winner == "villager" else ("狼人胜利" if winner == "werewolf" else "对局结束")
        curve.append({
            "step": step,
            "round": max(int(total_rounds or 0), 0),
            "checkpoint": CHECKPOINT_FINAL,
            "good_win_prob": final_prob,
            "alive_wolves": wolves,
            "alive_goods": goods,
            "event_label": label,
            "deaths": [],
        })
    return curve


def find_turning_points(curve: list[dict], top_n: int = 3) -> list[dict]:
    """按相邻节点胜率变化 |ΔP| 从大到小取 top_n，作为转折点候选交给 LLM 解读。

    返回结果按时间顺序（step 升序）排列，便于阅读。
    """
    pts: list[dict] = []
    for i in range(1, len(curve)):
        prev = curve[i - 1]["good_win_prob"]
        cur = curve[i]["good_win_prob"]
        delta = round(cur - prev, 4)
        if abs(delta) < 1e-9:
            continue
        node = curve[i]
        pts.append({
            "step": node["step"],
            "round": node["round"],
            "checkpoint": node["checkpoint"],
            "good_win_prob": cur,
            "delta": delta,
            "event_label": node["event_label"],
            "deaths": node["deaths"],
        })
    pts.sort(key=lambda x: abs(x["delta"]), reverse=True)
    top = pts[:max(top_n, 0)]
    top.sort(key=lambda x: x["step"])
    return top
