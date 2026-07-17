"""Public event protocol regression tests."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.public_events import to_public_event


def test_to_public_event_exposes_stable_id_and_server_order_for_public_event():
    """The REST snapshot and WS payload share one stable identity and order key."""
    event = SimpleNamespace(
        id="event-002",
        event_type="speech",
        event_data='{"content": "hello"}',
        phase="day",
        round_number=2,
        seat_number=3,
        created_at=datetime(2026, 7, 16, 12, 0, 1),
    )

    assert to_public_event(event) == {
        "event_id": "event-002",
        "event_order": "2026-07-16T12:00:01:event-002",
        "type": "speech",
        "data": {"round": 2, "seat": 3, "content": "hello"},
        "timestamp": "2026-07-16T12:00:01",
    }


def test_to_public_event_drops_private_night_event_instead_of_serializing_it():
    """Private night payloads cannot leak through the public snapshot or broadcast path."""
    event = SimpleNamespace(
        id="event-private",
        event_type="night_settle",
        event_data='{"deaths": [2], "role": "seer"}',
        phase="night",
        round_number=1,
        seat_number=2,
        created_at=datetime(2026, 7, 16, 12, 0, 2),
    )

    assert to_public_event(event) is None


def test_to_public_event_drops_private_hunter_payload_even_when_triggered_in_daytime():
    """A hunter's target remains private regardless of the phase in which it was selected."""
    event = SimpleNamespace(
        id="event-private-day",
        event_type="hunter_shot",
        event_data='{"target": 5, "trigger": "voted_out"}',
        phase="day",
        round_number=2,
        seat_number=1,
        created_at=datetime(2026, 7, 16, 12, 0, 3),
    )

    assert to_public_event(event) is None


@pytest.mark.parametrize("event_type", ["night_kill", "night_verify", "night_save", "night_poison", "night_witch_skip"])
def test_to_public_event_drops_each_private_night_action(event_type):
    """Anonymous REST and WS consumers cannot infer action count or role survival."""
    event = SimpleNamespace(
        id=f"event-{event_type}",
        event_type=event_type,
        event_data='{"target": 2, "role": "werewolf", "save_available": true}',
        phase="night",
        round_number=3,
        seat_number=1,
        created_at=datetime(2026, 7, 16, 12, 0, 4),
    )

    assert to_public_event(event) is None


def test_to_public_event_allows_only_the_single_fixed_night_start_notice():
    """The night-start node emits the sole anonymous event for a round."""
    event = SimpleNamespace(
        id="event-night-start",
        event_type="night_phase",
        event_data='{"role": "werewolf", "target": 2}',
        phase="night",
        round_number=3,
        seat_number=None,
        created_at=datetime(2026, 7, 16, 12, 0, 4),
    )

    assert to_public_event(event) == {
        "event_id": "event-night-start",
        "event_order": "2026-07-16T12:00:04:event-night-start",
        "type": "night_phase",
        "data": {"round": 3},
        "timestamp": "2026-07-16T12:00:04",
    }
