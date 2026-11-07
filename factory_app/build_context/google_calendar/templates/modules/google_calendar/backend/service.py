"""
The signed-in user's own Google Calendar, through the Google account they connected.
DO NOT EDIT: regenerated on framework upgrades. Put app-specific changes in handler.py.

Every call is made as the requesting user with their own grant (see
mozaiksai.core.connected_accounts). Nothing is stored here, and no token ever
appears in a result, an error or a log line.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mozaiksai.core.connected_accounts import account_request, google_api_base_url
from mozaiksai.core.runtime.composition.module_executor import ModuleInputValidationError

CONNECTION_ID = "google_calendar"
_PRIMARY_EVENTS = "/calendar/v3/calendars/primary/events"


class GoogleCalendarError(RuntimeError):
    """Google Calendar refused or failed the request."""


def _parse_time(value: str, field: str, time_zone: str | None) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ModuleInputValidationError(f"{field} must be an ISO 8601 date-time") from exc
    if parsed.tzinfo is None and not time_zone:
        raise ModuleInputValidationError(f"{field} needs a UTC offset, or give time_zone")
    return parsed


def _event_time(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if value.get("dateTime"):
        return {"date_time": value["dateTime"], "time_zone": value.get("timeZone")}
    return {"date": value.get("date")}


def _event(item: dict[str, Any]) -> dict[str, Any]:
    # Only what the user's own view needs: no attendee addresses or conference secrets.
    start = item.get("start") if isinstance(item.get("start"), dict) else {}
    return {
        "id": item.get("id"),
        "summary": item.get("summary") or "(no title)",
        "start": _event_time(item.get("start")),
        "end": _event_time(item.get("end")),
        "all_day": bool(start.get("date")) and not start.get("dateTime"),
        "location": item.get("location"),
        "status": item.get("status"),
        "html_link": item.get("htmlLink"),
    }


class GoogleCalendarService:
    async def _call(self, ctx, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await account_request(ctx, CONNECTION_ID, method, f"{google_api_base_url()}{path}", **kwargs)
        if response.status_code >= 400:
            raise GoogleCalendarError(f"Google Calendar refused the request (HTTP {response.status_code}).")
        try:
            data = response.json()
        except ValueError as exc:
            raise GoogleCalendarError("Google Calendar returned an unreadable response.") from exc
        if not isinstance(data, dict):
            raise GoogleCalendarError("Google Calendar returned an unexpected response.")
        return data

    async def list_upcoming_events(self, ctx, *, max_results: int = 10, days_ahead: int = 7) -> dict[str, Any]:
        now = datetime.now(UTC)
        data = await self._call(ctx, "GET", _PRIMARY_EVENTS, params={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days_ahead)).isoformat(),
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        })
        events = [_event(item) for item in data.get("items") or [] if isinstance(item, dict)]
        return {"events": events, "total": len(events)}

    async def create_event(
        self,
        ctx,
        *,
        summary: str,
        start: str,
        end: str,
        time_zone: str | None = None,
        description: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        summary = str(summary or "").strip()
        if not summary:
            raise ModuleInputValidationError("summary is required")
        time_zone = str(time_zone or "").strip() or None
        if time_zone:
            try:
                ZoneInfo(time_zone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ModuleInputValidationError("time_zone must be an IANA time zone such as Europe/Paris") from exc
        start_at = _parse_time(start, "start", time_zone)
        end_at = _parse_time(end, "end", time_zone)
        zone = ZoneInfo(time_zone) if time_zone else None
        if (end_at if end_at.tzinfo else end_at.replace(tzinfo=zone)) <= (
            start_at if start_at.tzinfo else start_at.replace(tzinfo=zone)
        ):
            raise ModuleInputValidationError("end must be after start")
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start_at.isoformat()},
            "end": {"dateTime": end_at.isoformat()},
        }
        if time_zone:
            body["start"]["timeZone"] = time_zone
            body["end"]["timeZone"] = time_zone
        if description:
            body["description"] = str(description)
        if location:
            body["location"] = str(location)
        created = await self._call(ctx, "POST", _PRIMARY_EVENTS, json=body)
        return {"success": True, "event": _event(created)}
