from __future__ import annotations


def validate_thread_participants(participant_ids: list[str]) -> None:
    unique_ids = [participant_id for participant_id in dict.fromkeys(participant_ids) if participant_id]
    if len(unique_ids) < 2:
        raise ValueError("communications threads require at least two distinct participants")


def validate_message_body(body: str) -> None:
    if not body.strip():
        raise ValueError("communications messages require a non-empty body")


def validate_announcement_scope(audience_scope: str) -> None:
    allowed = {"organization", "role", "thread"}
    if audience_scope not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"announcement scope must be one of: {allowed_text}")


__all__ = [
    "validate_announcement_scope",
    "validate_message_body",
    "validate_thread_participants",
]