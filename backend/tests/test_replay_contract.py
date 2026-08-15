"""回放契约单元测试 — 验证 ReplayStep 结构、事件排序和描述器正确性。"""

import json

from app.api.replay_router import _event_description
from app.api.schemas.game_schemas import ReplayStep


class TestReplayStepSchema:
    """ReplayStep schema 必须包含 round 字段。"""

    def test_replay_step_has_round_field(self):
        """ReplayStep 必须有 round 字段用于前端按轮次分组。"""
        step = ReplayStep(
            step_index=0,
            round=1,
            phase="night",
            event_type="night_phase",
            description="夜晚开始",
        )
        assert step.round == 1


class TestEventDescription:
    """_event_description 必须从 GameEvent.seat_number 读取执行者座位。"""

    def test_speech_uses_seat_number_not_event_data_seat(self):
        """发言描述应从 event.seat_number 读取，而非 event_data.seat。"""
        from app.models.game import GameEvent

        event = GameEvent(
            game_id="g1",
            round_number=1,
            phase="day",
            event_type="speech",
            seat_number=3,
            event_data=json.dumps({"content": "我是好人"}),
        )
        desc = _event_description(event)
        assert "3" in desc
        assert "?" not in desc

    def test_vote_uses_seat_number_not_event_data_seat(self):
        """投票描述应从 event.seat_number 读取。"""
        from app.models.game import GameEvent

        event = GameEvent(
            game_id="g1",
            round_number=1,
            phase="day",
            event_type="vote",
            seat_number=5,
            event_data=json.dumps({"target": 2}),
        )
        desc = _event_description(event)
        assert "5" in desc
        assert "?" not in desc

    def test_eliminate_uses_seat_number_not_event_data_seat(self):
        """淘汰描述应从 event.seat_number 读取。"""
        from app.models.game import GameEvent

        event = GameEvent(
            game_id="g1",
            round_number=1,
            phase="day",
            event_type="eliminate",
            seat_number=4,
            event_data=json.dumps({"is_pk": False}),
        )
        desc = _event_description(event)
        assert "4" in desc
        assert "?" not in desc

    def test_last_words_uses_seat_number(self):
        """遗言描述应从 event.seat_number 读取。"""
        from app.models.game import GameEvent

        event = GameEvent(
            game_id="g1",
            round_number=1,
            phase="day",
            event_type="last_words",
            seat_number=2,
            event_data=json.dumps({"content": "我是好人"}),
        )
        desc = _event_description(event)
        assert "2" in desc
        assert "?" not in desc

    def test_hunter_shot_description_includes_target(self):
        """猎人开枪描述应从 event_data.target 读取目标。"""
        from app.models.game import GameEvent

        event = GameEvent(
            game_id="g1",
            round_number=1,
            phase="night",
            event_type="hunter_shot",
            seat_number=7,
            event_data=json.dumps({"target": 3, "trigger": "killed_by_werewolf"}),
        )
        desc = _event_description(event)
        assert "3" in desc

    def test_guard_description_includes_target(self):
        """守卫描述应显示目标。"""
        from app.models.game import GameEvent

        event = GameEvent(
            game_id="g1",
            round_number=1,
            phase="night",
            event_type="night_guard",
            seat_number=6,
            event_data=json.dumps({"target": 3}),
        )
        desc = _event_description(event)
        assert "3" in desc
