"""胜率引擎单测：终局收敛 / 单调性 / 神职权重 / 曲线首尾 / 转折点。"""

from app.services.win_probability import (
    build_win_curve,
    compute_good_win_prob,
    find_turning_points,
)


def _p(seat, role, alive=True, dr=None, dp=None):
    return {
        "seat_number": seat, "role": role, "is_alive": alive,
        "death_round": dr, "death_phase": dp,
    }


# ─── compute_good_win_prob ───────────────────────────────

def test_no_wolves_is_certain_good_win():
    assert compute_good_win_prob(["villager", "seer"]) == 1.0


def test_wolves_reach_or_exceed_goods_is_certain_loss():
    assert compute_good_win_prob(["werewolf", "villager"]) == 0.0
    assert compute_good_win_prob(["werewolf", "werewolf", "villager"]) == 0.0


def test_prob_is_between_zero_and_one_in_midgame():
    p = compute_good_win_prob(["werewolf", "villager", "villager", "seer"])
    assert 0.0 < p < 1.0


def test_killing_wolf_raises_good_win_prob():
    before = compute_good_win_prob(["werewolf", "werewolf", "villager", "villager", "seer"])
    after = compute_good_win_prob(["werewolf", "villager", "villager", "seer"])
    assert after > before


def test_losing_seer_drops_more_than_losing_villager():
    lose_villager = compute_good_win_prob(["werewolf", "villager", "villager", "seer"])
    lose_seer = compute_good_win_prob(["werewolf", "villager", "villager", "villager"])
    assert lose_seer < lose_villager


def test_accepts_role_enum_like_objects():
    class _R:
        value = "werewolf"
    # 兼容枚举：1 狼 2 好人（含枚举对象）
    p = compute_good_win_prob([_R(), "villager", "seer"])
    assert 0.0 < p < 1.0


# ─── build_win_curve ─────────────────────────────────────

def test_curve_starts_full_alive_and_ends_with_winner():
    players = [
        _p(1, "werewolf", alive=False, dr=2, dp="day"),
        _p(2, "werewolf", alive=False, dr=3, dp="day"),
        _p(3, "seer"),
        _p(4, "villager"),
        _p(5, "villager", alive=False, dr=1, dp="night"),
        _p(6, "witch"),
    ]
    curve = build_win_curve(players, total_rounds=3, winner="villager")

    assert curve[0]["checkpoint"] == "start"
    assert curve[0]["alive_wolves"] == 2
    assert curve[0]["alive_goods"] == 4
    assert curve[0]["good_win_prob"] < 1.0

    assert curve[-1]["checkpoint"] == "final"
    assert curve[-1]["good_win_prob"] == 1.0
    assert curve[-1]["event_label"] == "好人胜利"


def test_curve_has_two_nodes_per_round():
    players = [_p(1, "werewolf"), _p(2, "villager"), _p(3, "villager")]
    curve = build_win_curve(players, total_rounds=2, winner=None)
    # start + 2*(after_night + after_day) = 5
    assert len(curve) == 5
    checkpoints = [c["checkpoint"] for c in curve]
    assert checkpoints == ["start", "after_night", "after_day", "after_night", "after_day"]


def test_night_death_reflected_at_after_night_node():
    players = [
        _p(1, "werewolf"),
        _p(2, "villager", alive=False, dr=1, dp="night"),
        _p(3, "villager"),
    ]
    curve = build_win_curve(players, total_rounds=1, winner="werewolf")
    start = curve[0]
    after_night = next(c for c in curve if c["checkpoint"] == "after_night")

    assert start["alive_goods"] == 2
    assert after_night["alive_goods"] == 1
    assert after_night["deaths"] == [2]
    assert after_night["good_win_prob"] < start["good_win_prob"]


def test_werewolf_win_final_converges_to_zero():
    players = [
        _p(1, "werewolf"),
        _p(2, "werewolf"),
        _p(3, "villager", alive=False, dr=1, dp="night"),
        _p(4, "villager", alive=False, dr=1, dp="day"),
    ]
    curve = build_win_curve(players, total_rounds=1, winner="werewolf")
    assert curve[-1]["good_win_prob"] == 0.0
    assert curve[-1]["event_label"] == "狼人胜利"


# ─── find_turning_points ─────────────────────────────────

def test_turning_points_sorted_and_limited():
    players = [
        _p(1, "werewolf", alive=False, dr=2, dp="day"),
        _p(2, "werewolf", alive=False, dr=3, dp="day"),
        _p(3, "seer"),
        _p(4, "villager"),
        _p(5, "villager", alive=False, dr=1, dp="night"),
        _p(6, "witch"),
    ]
    curve = build_win_curve(players, total_rounds=3, winner="villager")
    turns = find_turning_points(curve, top_n=2)

    assert len(turns) <= 2
    assert turns == sorted(turns, key=lambda x: x["step"])
    # 每个转折点带 delta 与标签
    assert all("delta" in t and "event_label" in t for t in turns)


def test_turning_points_empty_for_flat_curve():
    players = [_p(1, "villager"), _p(2, "villager")]
    curve = build_win_curve(players, total_rounds=2, winner=None)
    # 全程无狼、无人死亡 → 胜率恒为 1.0，无转折点
    assert find_turning_points(curve) == []
