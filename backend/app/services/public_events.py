"""Canonical serialization for events that are safe to expose publicly."""

import json
from typing import Any


_PRIVATE_EVENT_TYPES = {"night_settle", "night_guard", "hunter_shot", "hunter_revenge"}


def to_public_event(event: Any) -> dict[str, Any] | None:
    """Return the single public protocol representation, or ``None`` for private events."""
    if event.event_type.startswith("private_") or event.event_type in _PRIVATE_EVENT_TYPES:
        return None

    if event.phase == "night":
        if event.event_type != "night_phase":
            return None
        public_type = "night_phase"
        data = {"round": event.round_number}
    else:
        public_type = event.event_type
        data = {
            "round": event.round_number,
            "seat": event.seat_number,
            **json.loads(event.event_data or "{}"),
        }

    timestamp = event.created_at.isoformat()
    return {
        "event_id": event.id,
        "event_order": f"{timestamp}:{event.id}",
        "type": public_type,
        "data": data,
        "timestamp": timestamp,
    }
