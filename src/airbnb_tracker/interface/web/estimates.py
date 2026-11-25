"""Rough run-time estimates shown before starting a job (measured on real runs)."""

from __future__ import annotations

from dataclasses import dataclass

from airbnb_tracker.infrastructure.config import AppSettings

# Observed: ~17 s per search with complete coverage, ~6 s without; ~3.5 s per request.
_SEARCH_SECONDS = {True: 17.0, False: 6.0}
_REQUEST_OVERHEAD_SECONDS = 0.5
_ENRICH_REQUESTS_PER_LISTING = 3  # page, calendar, reviews


@dataclass(frozen=True, slots=True)
class RunEstimate:
    searches: int
    listings_to_enrich: int
    scan_minutes: float
    enrich_minutes: float

    @property
    def total_minutes(self) -> float:
        return self.scan_minutes + self.enrich_minutes


def estimate(settings: AppSettings, searches: int) -> RunEstimate:
    delay = settings.scraping.min_delay_seconds
    per_search = _SEARCH_SECONDS[settings.search.complete_coverage] + delay
    listings = (settings.enrich.competitors or 50) + (1 if settings.my_listing.listing_id else 0)
    listings = min(listings, settings.enrich.max_listings_per_run)
    per_listing = _ENRICH_REQUESTS_PER_LISTING * (delay + _REQUEST_OVERHEAD_SECONDS)
    return RunEstimate(
        searches=searches,
        listings_to_enrich=listings,
        scan_minutes=searches * per_search / 60,
        enrich_minutes=listings * per_listing / 60,
    )
