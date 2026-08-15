"""AI 狼人杀 — 信息隔离（Context Filter）单元测试

测试技术方案 §5.2 定义的可见性矩阵：
| 信息类型              | 狼人 | 预言家 | 女巫 | 村民 |
|:--                    |:--: | :--:  |:--: | :--:|
| 自己的角色             | YES | YES   | YES | YES |
| 狼人同伴身份           | YES | NO    | NO  | NO  |
| 预言家查验结果         | NO  | YES   | NO  | NO  |
| 女巫药水状态           | NO  | NO    | YES | NO  |
| 被击杀者(女巫视角)     | NO  | NO    | YES | NO  |
| 自己选的击杀目标       | YES | NO    | NO  | NO  |
| 公开死亡/发言/投票     | YES | YES   | YES | YES |
| 其他玩家角色           | NO  | NO    | NO  | NO  |

测试结构：AAA 模式（Arrange-Act-Assert）
"""

import pytest

from app.agent.context_filter import filter_context

# ─── 测试数据工厂 ─────────────────────────────────────────

def _make_game_state() -> dict:
    """构建一个完整的游戏状态用于测试

    6 个玩家：1号狼人, 2号狼人, 3号村民, 4号村民, 5号预言家, 6号女巫
    """
    return {
        "game_id": "test-001",
        "current_round": 2,
        "players": [
            {"seat_number": 1, "player_name": "AI-1", "player_type": "ai", "role": "werewolf", "is_alive": True,
             "death_round": None, "death_phase": None, "death_reason": None},
            {"seat_number": 2, "player_name": "AI-2", "player_type": "ai", "role": "werewolf", "is_alive": True,
             "death_round": None, "death_phase": None, "death_reason": None},
            {"seat_number": 3, "player_name": "AI-3", "player_type": "ai", "role": "villager", "is_alive": True,
             "death_round": None, "death_phase": None, "death_reason": None},
            {"seat_number": 4, "player_name": "AI-4", "player_type": "ai", "role": "villager", "is_alive": False,
             "death_round": 1, "death_phase": "night", "death_reason": "killed_by_werewolf"},
            {"seat_number": 5, "player_name": "AI-5", "player_type": "ai", "role": "seer", "is_alive": True,
             "death_round": None, "death_phase": None, "death_reason": None},
            {"seat_number": 6, "player_name": "AI-6", "player_type": "ai", "role": "witch", "is_alive": True,
             "death_round": None, "death_phase": None, "death_reason": None},
        ],
        "werewolf_seats": [1, 2],
        "seer_seat": 5,
        "witch_seat": 6,
        # 夜晚数据
        "night_kill_target": 3,
        "night_seer_target": 1,
        "night_seer_result": "werewolf",
        "night_witch_action": "skip",
        "night_witch_target": None,
        "night_deaths": [],
        # 女巫药水
        "witch_save_used": True,
        "witch_poison_used": False,
        # 白天数据
        "speeches": [
            {"seat": 1, "content": "我觉得3号很可疑"},
            {"seat": 3, "content": "我是好人，不要投我"},
        ],
        "votes": {1: 3, 3: 1},
        "eliminated_seat": None,
    }


# ================================================================
# 公共信息（所有角色都可见）
# ================================================================

class TestPublicInfo:
    """测试所有角色都能看到公共信息"""

    @pytest.mark.parametrize("role,seat", [
        ("werewolf", 1), ("werewolf", 2),
        ("villager", 3), ("villager", 4),
        ("seer", 5), ("witch", 6),
    ])
    def test_all_roles_see_public_info(self, role, seat):
        """所有角色都应看到：轮次、存活状态、历史发言、历史投票"""
        state = _make_game_state()
        result = filter_context(state, seat, role, "speech")

        assert result["current_round"] == 2
        assert len(result["players"]) == 6
        # 存活状态可见
        assert result["players"][0]["is_alive"] is True   # 1号存活
        assert result["players"][3]["is_alive"] is False   # 4号已死
        # 历史发言可见
        assert len(result["speeches"]) == 2
        # 历史投票可见
        assert result["votes"] == {1: 3, 3: 1}

    @pytest.mark.parametrize("role,seat", [
        ("werewolf", 1), ("villager", 3), ("seer", 5), ("witch", 6),
    ])
    def test_no_player_roles_exposed(self, role, seat):
        """任何角色都不应看到其他玩家的角色"""
        state = _make_game_state()
        result = filter_context(state, seat, role, "speech")

        for p in result["players"]:
            assert "role" not in p, f"玩家 {p['seat_number']} 的角色不应被暴露"

    def test_filter_context_rejects_forged_role_for_real_seat(self):
        """调用方传入的 role 必须与 seat 的真实角色一致，防止越权过滤。"""
        state = _make_game_state()

        with pytest.raises(ValueError, match="真实角色"):
            filter_context(state, 5, "werewolf", "kill")

    def test_guard_context_contains_only_guard_contract_without_wolf_or_witch_info(self):
        """守卫上下文包含候选与上夜目标，但不泄露其他角色私密字段。"""
        state = _make_game_state()
        state["players"].append({"seat_number": 7, "player_name": "AI-7", "player_type": "ai", "role": "guard", "is_alive": True})
        state["guard_last_target"] = 3
        state["allowed_target_seats"] = [1, 2, 5, 6, 7]

        result = filter_context(state, 7, "guard", "guard")

        assert result["guard_last_target"] == 3
        assert result["allowed_target_seats"] == [1, 2, 5, 6, 7]
        assert "werewolf_companions" not in result
        assert "seer_results" not in result
        assert "witch_save_used" not in result

    def test_hunter_context_contains_trigger_skip_and_allowed_targets_only(self):
        """猎人上下文包含触发原因、可跳过与合法目标，不泄露守卫/女巫/狼人私密信息。"""
        state = _make_game_state()
        state["players"].append({"seat_number": 7, "player_name": "AI-7", "player_type": "ai", "role": "hunter", "is_alive": False})
        state["pending_hunter_shot"] = {"seat_number": 7, "trigger": "voted_out"}
        state["allowed_target_seats"] = [1, 2, 3, 5, 6]
        state["can_skip"] = True

        result = filter_context(state, 7, "hunter", "hunter_shoot")

        assert result["hunter_can_shoot"] is True
        assert result["hunter_trigger"] == "voted_out"
        assert result["can_skip"] is True
        assert result["allowed_target_seats"] == [1, 2, 3, 5, 6]
        assert "guard_last_target" not in result
        assert "werewolf_companions" not in result


# ================================================================
# 狼人专属信息
# ================================================================

class TestWerewolfVisibility:
    """测试狼人角色的信息可见性"""

    def test_werewolf_sees_companions(self):
        """狼人应看到同伴的座位号"""
        state = _make_game_state()
        result = filter_context(state, 1, "werewolf", "kill")

        assert "werewolf_companions" in result
        assert result["werewolf_companions"] == [2]  # 1号狼人的同伴是2号

    def test_werewolf_sees_own_kill_target(self):
        """狼人应看到自己之前选择的击杀目标"""
        state = _make_game_state()
        result = filter_context(state, 1, "werewolf", "kill")

        assert result.get("last_kill_target") == 3

    def test_werewolf_cannot_see_seer_results(self):
        """狼人不应看到预言家的查验结果"""
        state = _make_game_state()
        result = filter_context(state, 1, "werewolf", "kill")

        assert "seer_results" not in result or result["seer_results"] == []

    def test_werewolf_cannot_see_witch_info(self):
        """狼人不应看到女巫的药水状态和被击杀者信息"""
        state = _make_game_state()
        result = filter_context(state, 1, "werewolf", "kill")

        assert "witch_save_used" not in result
        assert "witch_poison_used" not in result
        assert "night_kill_target" not in result  # 女巫视角的被击杀者


# ================================================================
# 预言家专属信息
# ================================================================

class TestSeerVisibility:
    """测试预言家角色的信息可见性"""

    def test_seer_sees_verify_results(self):
        """预言家应看到自己的历史查验结果"""
        state = _make_game_state()
        result = filter_context(state, 5, "seer", "verify")

        assert "seer_results" in result
        assert len(result["seer_results"]) >= 1
        # 应包含：查验了1号，结果是狼人
        assert any(r["target"] == 1 and r["result"] == "werewolf" for r in result["seer_results"])

    def test_seer_cannot_see_werewolf_companions(self):
        """预言家不应看到狼人的同伴信息"""
        state = _make_game_state()
        result = filter_context(state, 5, "seer", "verify")

        assert "werewolf_companions" not in result

    def test_seer_cannot_see_witch_info(self):
        """预言家不应看到女巫的药水状态"""
        state = _make_game_state()
        result = filter_context(state, 5, "seer", "verify")

        assert "witch_save_used" not in result
        assert "witch_poison_used" not in result


# ================================================================
# 女巫专属信息
# ================================================================

class TestWitchVisibility:
    """测试女巫角色的信息可见性"""

    def test_witch_sees_potion_status(self):
        """女巫应看到自己的药水使用状态"""
        state = _make_game_state()
        result = filter_context(state, 6, "witch", "save")

        assert result["witch_save_used"] is True
        assert result["witch_poison_used"] is False

    def test_witch_sees_kill_victim(self):
        """女巫（在夜晚行动时）应看到被狼人击杀的玩家"""
        state = _make_game_state()
        result = filter_context(state, 6, "witch", "save")

        assert result["night_kill_target"] == 3  # 被击杀的是3号

    def test_witch_cannot_see_werewolf_companions(self):
        """女巫不应看到狼人同伴信息"""
        state = _make_game_state()
        result = filter_context(state, 6, "witch", "save")

        assert "werewolf_companions" not in result

    def test_witch_cannot_see_seer_results(self):
        """女巫不应看到预言家查验结果"""
        state = _make_game_state()
        result = filter_context(state, 6, "witch", "save")

        assert "seer_results" not in result or result["seer_results"] == []


# ================================================================
# 村民信息（只能看到公共信息）
# ================================================================

class TestVillagerVisibility:
    """测试村民角色的信息可见性（最受限）"""

    def test_villager_only_sees_public(self):
        """村民只应看到公共信息，无任何角色专属数据"""
        state = _make_game_state()
        result = filter_context(state, 3, "villager", "speech")

        # 公共信息可见
        assert result["current_round"] == 2
        assert len(result["players"]) == 6
        assert len(result["speeches"]) == 2

        # 无任何角色专属信息
        assert "werewolf_companions" not in result
        assert "seer_results" not in result or result["seer_results"] == []
        assert "witch_save_used" not in result
        assert "witch_poison_used" not in result
        assert "night_kill_target" not in result
        assert "last_kill_target" not in result


# ================================================================
# 输出过滤：确保玩家字典中不包含 role 字段
# ================================================================

class TestOutputSanitization:
    """测试输出数据的清洁度"""

    def test_player_dicts_have_no_role_field(self):
        """过滤后的玩家字典不应包含 role 字段（防止信息泄漏）"""
        state = _make_game_state()

        for seat, role in [(1, "werewolf"), (3, "villager"), (5, "seer"), (6, "witch")]:
            result = filter_context(state, seat, role, "speech")
            for p in result["players"]:
                assert "role" not in p, (
                    f"信息泄漏: {role}({seat}号) 看到了 {p['seat_number']}号 的角色"
                )

    def test_alive_list_provided(self):
        """过滤结果应包含便捷字段 alive_seats（存活座位号列表）"""
        state = _make_game_state()
        result = filter_context(state, 1, "werewolf", "kill")

        # 4号已死，所以存活座位号应排除4
        assert 4 not in result["alive_seats"]
        assert 1 in result["alive_seats"]


# ================================================================
# 平票 PK 状态（公开信息）
# 修复“只记得最终投票，不记得平票” bug
# ================================================================

class TestPkVisibility:
    """测试平票 PK 状态作为公开信息对所有人可见"""

    def test_pk_state_visible_to_all_roles(self):
        """is_pk / pk_seats 应作为公开信息对所有角色可见。"""
        state = _make_game_state()
        state["is_pk"] = True
        state["pk_seats"] = [2, 5]

        for seat, role in [(1, "werewolf"), (3, "villager"), (5, "seer"), (6, "witch")]:
            result = filter_context(state, seat, role, "vote")
            assert result["is_pk"] is True
            assert result["pk_seats"] == [2, 5]

    def test_no_pk_defaults_to_false(self):
        """未发生 PK 时 is_pk 应为 False，pk_seats 为空。"""
        state = _make_game_state()
        result = filter_context(state, 3, "villager", "vote")
        assert result["is_pk"] is False
        assert result["pk_seats"] == []
