"""Polite, fault-tolerant access to external sources.

``RequestGuard`` combines throttling and retries; the ``Resilient*`` classes are GoF
*Decorators* that add that behaviour to any gateway without modifying it (OCP).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from airbnb_tracker.application.dto import ListingDetails, SearchCriteria
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import (
    CalendarGateway,
    ListingDetailsGateway,
    ListingSearchGateway,
)
from airbnb_tracker.domain.entities import ListingOffer
from airbnb_tracker.domain.profile import CalendarDay
from airbnb_tracker.domain.value_objects import ListingId

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter:
    """Guarantees a minimum (slightly jittered) delay between consecutive requests."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        jitter_ratio: float = 0.3,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._min_interval = max(0.0, min_interval_seconds)
        self._jitter_ratio = jitter_ratio
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            interval = self._min_interval * (1 + self._jitter_ratio * self._rng())
            remaining = interval - (self._monotonic() - self._last_call)
            if remaining > 0:
                self._sleep(remaining)
        self._last_call = self._monotonic()


class RetryPolicy:
    """Retries GatewayErrors with exponential backoff."""

    def __init__(
        self,
        max_attempts: int = 3,
        backoff_seconds: float = 5.0,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff_seconds
        self._sleep = sleep

    def run(self, operation: Callable[[], T]) -> T:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return operation()
            except GatewayError as error:
                if attempt == self._max_attempts:
                    raise
                delay = self._backoff * 2 ** (attempt - 1)
                logger.info("Attempt %s failed (%s); retrying in %.1fs", attempt, error, delay)
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover


class RequestGuard:
    def __init__(self, rate_limiter: RateLimiter, retry_policy: RetryPolicy) -> None:
        self._rate_limiter = rate_limiter
        self._retry_policy = retry_policy

    def call(self, operation: Callable[[], T]) -> T:
        def throttled() -> T:
            self._rate_limiter.wait()
            return operation()

        return self._retry_policy.run(throttled)


class ResilientSearchGateway:
    def __init__(self, inner: ListingSearchGateway, guard: RequestGuard) -> None:
        self._inner = inner
        self._guard = guard

    def search(self, criteria: SearchCriteria) -> list[ListingOffer]:
        return self._guard.call(lambda: self._inner.search(criteria))


class ResilientDetailsGateway:
    def __init__(self, inner: ListingDetailsGateway, guard: RequestGuard) -> None:
        self._inner = inner
        self._guard = guard

    def get_details(self, listing_id: ListingId) -> ListingDetails:
        return self._guard.call(lambda: self._inner.get_details(listing_id))


class ResilientCalendarGateway:
    def __init__(self, inner: CalendarGateway, guard: RequestGuard) -> None:
        self._inner = inner
        self._guard = guard

    def get_calendar(self, listing_id: ListingId) -> list[CalendarDay]:
        return self._guard.call(lambda: self._inner.get_calendar(listing_id))
