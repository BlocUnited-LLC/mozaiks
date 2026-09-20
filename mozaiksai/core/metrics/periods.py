"""Shared period and comparison-window model for owner analytics.

One deterministic implementation of "last 30 days vs the previous 30 days"
so no widget re-implements date math. All windows are half-open UTC ranges
``[since, until)`` with an equal-shape previous window for comparison:

- rolling windows (``7d``/``30d``/``90d``) compare against the immediately
  preceding window of the same length;
- calendar windows (``mtd``/``qtd``/``ytd``) compare against the same
  elapsed slice of the previous month/quarter/year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

PeriodId = Literal["7d", "30d", "90d", "mtd", "qtd", "ytd"]

PERIOD_IDS: tuple[PeriodId, ...] = ("7d", "30d", "90d", "mtd", "qtd", "ytd")

_ROLLING_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90}

_PERIOD_LABELS: dict[str, str] = {
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "mtd": "Month to date",
    "qtd": "Quarter to date",
    "ytd": "Year to date",
}

_COMPARISON_LABELS: dict[str, str] = {
    "7d": "vs previous 7 days",
    "30d": "vs previous 30 days",
    "90d": "vs previous 90 days",
    "mtd": "vs same point last month",
    "qtd": "vs same point last quarter",
    "ytd": "vs same point last year",
}


class PeriodError(ValueError):
    """Raised for unknown period identifiers."""


@dataclass(frozen=True)
class PeriodWindow:
    """A resolved reporting window with its comparison window."""

    period_id: PeriodId
    label: str
    comparison_label: str
    since: datetime
    until: datetime
    previous_since: datetime
    previous_until: datetime

    def to_payload(self) -> dict[str, str]:
        return {
            "id": self.period_id,
            "label": self.label,
            "comparison_label": self.comparison_label,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "previous_since": self.previous_since.isoformat(),
            "previous_until": self.previous_until.isoformat(),
        }


def _start_of_month(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_quarter(moment: datetime) -> datetime:
    quarter_month = ((moment.month - 1) // 3) * 3 + 1
    return moment.replace(
        month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _start_of_year(moment: datetime) -> datetime:
    return moment.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _shift_months(moment: datetime, months: int) -> datetime:
    """Shift a timestamp by whole months, clamping the day to the target month."""

    total = (moment.year * 12 + (moment.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    day = moment.day
    while day > 28:
        try:
            return moment.replace(year=year, month=month, day=day)
        except ValueError:
            day -= 1
    return moment.replace(year=year, month=month, day=day)


def resolve_period(period_id: str, *, now: datetime | None = None) -> PeriodWindow:
    """Resolve a period identifier into concrete UTC windows."""

    normalized = str(period_id or "").strip().lower()
    if normalized not in PERIOD_IDS:
        raise PeriodError(
            f"unknown period '{period_id}'; expected one of {', '.join(PERIOD_IDS)}"
        )

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)

    if normalized in _ROLLING_DAYS:
        days = _ROLLING_DAYS[normalized]
        since = moment - timedelta(days=days)
        previous_since = since - timedelta(days=days)
        previous_until = since
    elif normalized == "mtd":
        since = _start_of_month(moment)
        previous_since = _start_of_month(_shift_months(since, -1))
        previous_until = _shift_months(moment, -1)
    elif normalized == "qtd":
        since = _start_of_quarter(moment)
        previous_since = _start_of_quarter(_shift_months(since, -3))
        previous_until = _shift_months(moment, -3)
    else:  # ytd
        since = _start_of_year(moment)
        previous_since = _start_of_year(_shift_months(since, -12))
        previous_until = _shift_months(moment, -12)

    return PeriodWindow(
        period_id=normalized,  # type: ignore[arg-type]
        label=_PERIOD_LABELS[normalized],
        comparison_label=_COMPARISON_LABELS[normalized],
        since=since,
        until=moment,
        previous_since=previous_since,
        previous_until=previous_until,
    )


__all__ = ["PERIOD_IDS", "PeriodError", "PeriodId", "PeriodWindow", "resolve_period"]
