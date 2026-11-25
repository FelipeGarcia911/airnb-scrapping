"""Which (check-in, length) combinations to sample prices on."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from airbnb_tracker.domain.exceptions import InvalidValueError
from airbnb_tracker.domain.value_objects import StayPeriod


def plan_stays(
    today: date,
    durations: Sequence[int],
    *,
    first_check_in_days: int = 7,
    every_days: int = 30,
    horizon_days: int = 180,
) -> list[StayPeriod]:
    """Every duration for each check-in date between ``first_check_in_days`` and the horizon.

    Location, filters and guests stay constant; only dates vary, so prices are comparable.
    """
    if every_days < 1 or horizon_days < first_check_in_days:
        raise InvalidValueError("every_days must be >= 1 and horizon >= first check-in")
    check_ins = [
        today + timedelta(days=offset)
        for offset in range(first_check_in_days, horizon_days + 1, every_days)
    ]
    return [StayPeriod.starting(check_in, nights) for check_in in check_ins for nights in durations]
