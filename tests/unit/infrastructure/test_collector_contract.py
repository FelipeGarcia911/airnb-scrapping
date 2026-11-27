"""The collector against saved, sanitized pyairbnb responses: no live Airbnb needed.

Run before adopting a new pyairbnb version (see tests/fixtures/airbnb/README.md).
"""

from __future__ import annotations

import inspect
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyairbnb
import pytest
from tests.conftest import CENTER, NOW, FixedClock, make_listing

from airbnb_tracker.application.dto import (
    FailureKind,
    SearchConditions,
    SearchFilters,
    SearchStatus,
)
from airbnb_tracker.application.use_cases import (
    EnrichCommand,
    EnrichGateways,
    EnrichListingsUseCase,
    EnrichPart,
    EnrichRepositories,
    ScanMarketCommand,
    ScanMarketUseCase,
)
from airbnb_tracker.application.use_cases.scan_market import ScanSummary
from airbnb_tracker.domain.entities import ListingOffer, Snapshot
from airbnb_tracker.domain.price_reading import Exclusion
from airbnb_tracker.domain.profile import AvailabilitySummary, CalendarDay, CalendarSnapshot
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod
from airbnb_tracker.infrastructure.airbnb.gateways import (
    PyAirbnbCalendarGateway,
    PyAirbnbDetailsGateway,
    PyAirbnbReviewGateway,
    PyAirbnbSearchGateway,
    RawSearch,
)
from airbnb_tracker.infrastructure.persistence import InMemoryRepository

FIXTURES = Path(__file__).parents[2] / "fixtures" / "airbnb"
STAY = StayPeriod(date(2027, 1, 15), date(2027, 1, 22))  # 7 nights
CONDITIONS = SearchConditions.build(
    guests=2, currency="COP", center=CENTER, radius_km=3, url=None, filters={},
    kept_listings=None, complete_coverage=True,
)  # fmt: skip


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class SavedResponses:
    """A PyAirbnbClient answering with the saved responses (overridable per test)."""

    def __init__(
        self,
        *,
        search: list[Any] | None = None,
        truncated: bool = False,
        calendar: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._search = fixture("search_page.json") if search is None else search
        self._truncated = truncated
        self._calendar = fixture("calendar_months.json") if calendar is None else calendar
        self._metadata = fixture("listing_metadata.json") if metadata is None else metadata

    def search_all(self, **_kwargs: Any) -> RawSearch:
        return RawSearch(self._search, truncated=self._truncated)

    def search_all_from_url(self, url: str, currency: str, proxy_url: str) -> RawSearch:
        del url, currency, proxy_url
        return RawSearch(self._search, truncated=self._truncated)

    def get_reviews(self, room_url: str, proxy_url: str) -> list[dict[str, Any]]:
        del room_url, proxy_url
        return list(fixture("reviews.json"))

    def get_metadata(self, room_url: str, proxy_url: str) -> dict[str, Any]:
        del room_url, proxy_url
        return dict(self._metadata)

    def get_calendar(self, room_id: str, proxy_url: str) -> list[dict[str, Any]]:
        del room_id, proxy_url
        return list(self._calendar)


def scan(client: SavedResponses) -> tuple[ScanSummary, InMemoryRepository]:
    repo = InMemoryRepository()
    gateway = PyAirbnbSearchGateway(FixedClock(), client=client)
    command = ScanMarketCommand(
        stays=[STAY], currency="COP", conditions=CONDITIONS, center=CENTER, radius_km=3
    )
    return ScanMarketUseCase(gateway, repo, repo, FixedClock()).execute(command)[0], repo


def offers_by_id(repo: InMemoryRepository) -> dict[str, ListingOffer]:
    return {str(offer.listing.id): offer for offer in repo.all_snapshots()[0].offers}


# --- prices -------------------------------------------------------------------------


def test_search_page_counts_every_step() -> None:
    summary, _repo = scan(SavedResponses())
    counts = summary.counts
    assert counts.received == 11
    assert counts.parsed == 9  # one row without room_id, one with a text price
    assert counts.duplicates == 1
    assert counts.filtered_out == 1  # far outside the area
    assert counts.kept == 7
    assert counts.priced == 4  # stay total, nightly, weekly, monthly
    assert counts.price_excluded == 2  # unknown unit, nights mismatch
    assert summary.status is SearchStatus.PARTIAL  # some rows could not be interpreted


def test_search_page_prices_are_normalised_or_excluded_with_reason() -> None:
    _summary, repo = scan(SavedResponses())
    offers = offers_by_id(repo)
    nightly = {i: o.quote.nightly.amount for i, o in offers.items() if o.quote}
    assert nightly == {
        "1001": Decimal("140000"),  # 980,000 for 7 nights (discounted)
        "1002": Decimal("150000"),
        "1003": Decimal("128571.43"),  # 900,000 a week
        "1004": Decimal("120000"),  # 3,600,000 a 30-night month
    }
    assert offers["1001"].quote is not None
    assert offers["1001"].quote.discount_pct == Decimal("6.7")
    exclusions = {i: o.rejected.exclusion for i, o in offers.items() if o.rejected}
    assert exclusions == {"1005": Exclusion.UNKNOWN_UNIT, "1006": Exclusion.NIGHTS_MISMATCH}
    assert not offers["1007"].is_available  # no price shown


def test_changed_structure_is_a_format_failure() -> None:
    renamed = [
        {"id": r.get("room_id"), "pricing": r.get("price")} for r in fixture("search_page.json")
    ]
    summary, repo = scan(SavedResponses(search=renamed))
    assert (summary.status, summary.failure) == (SearchStatus.FAILED, FailureKind.FORMAT)
    assert repo.latest(STAY, CONDITIONS.key) is None  # nothing usable becomes the reference


def test_empty_and_truncated_searches() -> None:
    empty, _ = scan(SavedResponses(search=[]))
    assert empty.status is SearchStatus.EMPTY
    readable = [
        r
        for r in fixture("search_page.json")
        if r.get("room_id") and isinstance(r.get("price"), dict)
    ]
    truncated, _ = scan(SavedResponses(search=readable, truncated=True))
    assert truncated.status is SearchStatus.PARTIAL


# --- calendar -----------------------------------------------------------------------


def test_calendar_keeps_unknown_days_out_of_the_rate() -> None:
    days = PyAirbnbCalendarGateway(client=SavedResponses()).get_calendar(ListingId("1001"))
    assert {d.day.isoformat(): d.available for d in days} == {
        "2027-01-30": True,
        "2027-01-31": False,  # repeated with the same value: one day
        "2027-02-01": True,
        "2027-02-02": None,  # field missing
        "2027-02-03": None,  # not a boolean
        "2027-02-04": None,  # repeated with different values
    }
    captured = NOW.replace(year=2027, month=1, day=30)
    calendar = CalendarSnapshot(ListingId("1001"), captured, tuple(days))
    assert calendar.unavailable_pct(window_days=10) == pytest.approx(33.3)  # 1 of 3 known
    assert calendar.coverage_pct(window_days=10) == 30.0  # 3 of 10 days known


def test_calendar_with_changed_structure_measures_nothing() -> None:
    renamed = [{"month": 1, "dayList": m.get("days")} for m in fixture("calendar_months.json")]
    client = SavedResponses(calendar=renamed)
    days = PyAirbnbCalendarGateway(client=client).get_calendar(ListingId("1001"))
    calendar = CalendarSnapshot(ListingId("1001"), NOW, tuple(days))
    assert calendar.unavailable_pct(window_days=30) is None  # unknown, never "0% booked"
    assert calendar.coverage_pct(window_days=30) == 0.0


# --- listing page and reviews --------------------------------------------------------


def test_listing_page() -> None:
    details = PyAirbnbDetailsGateway(client=SavedResponses()).get_details(ListingId("1001"))
    listing = details.listing
    assert (listing.bedrooms, listing.beds, listing.bathrooms) == (2, 2, 2.0)
    assert listing.property_type == "Entire rental unit"
    assert (listing.rating, listing.review_count) == (4.85, 20)
    assert {a.name for a in details.amenities} == {"Pool", "Gym", "Wifi"}  # not the dryer
    assert details.person_capacity == 4 and details.is_superhost
    assert details.description == "Two-bedroom apartment.\nPool and gym."


def test_listing_page_with_missing_fields() -> None:
    sparse = {"title": "", "rating": None, "coordinates": {"latitude": 0, "longitude": 0}}
    client = SavedResponses(metadata=sparse)
    details = PyAirbnbDetailsGateway(client=client).get_details(ListingId("1001"))
    assert details.listing.name == "1001"
    assert details.listing.location is None and details.listing.rating is None
    assert details.amenities == frozenset()


def test_reviews_keep_no_personal_data() -> None:
    reviews = PyAirbnbReviewGateway(client=SavedResponses()).get_reviews(ListingId("1001"))
    assert [r.review_id for r in reviews] == ["r1", "r2"]  # rows without id or date skipped
    assert "removed" not in repr(reviews)


# --- pyairbnb API --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("function", "arguments"),
    [
        (pyairbnb.search_all, {"check_in", "check_out", "ne_lat", "ne_long", "sw_lat",
                               "sw_long", "zoom_value", "adults", "currency", "language",
                               "proxy_url", "place_type", "min_bedrooms", "min_beds",
                               "min_bathrooms", "price_min", "price_max", "complete"}),
        (pyairbnb.search_all_from_url, {"currency", "language", "proxy_url"}),
        (pyairbnb.get_calendar, {"api_key", "room_id", "proxy_url", "return_dataclass"}),
    ],
)  # fmt: skip
def test_pyairbnb_still_accepts_our_arguments(function: Any, arguments: set[str]) -> None:
    parameters = inspect.signature(function).parameters
    accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
    assert accepts_any or arguments <= set(parameters)


# --- price range and calendars after a real collection ------------------------------


class RecordingClient(SavedResponses):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_kwargs: dict[str, Any] = {}

    def search_all(self, **kwargs: Any) -> RawSearch:
        self.search_kwargs = kwargs
        return super().search_all(**kwargs)


def test_price_range_is_per_night_for_every_stay_length() -> None:
    """Airbnb compares its price filter with monthly prices from 28 nights on, which
    emptied every 30- and 90-night search. The range is applied here, per night."""
    month = StayPeriod(date(2027, 1, 15), date(2027, 2, 14))  # 30 nights
    amounts = (3_000_000, 4_500_000, 9_000_000)  # 100k, 150k and 300k a night
    rows = fixture("search_page.json")[:3]
    monthly = [
        {**row, "price": {"unit": {"qualifier": "monthly", "amount": amount}}}
        for row, amount in zip(rows, amounts, strict=True)
    ]
    client = RecordingClient(search=monthly)
    repo = InMemoryRepository()
    filters = SearchFilters(price_min=0, price_max=200_000)
    command = ScanMarketCommand(
        stays=[month], currency="COP", conditions=CONDITIONS, filters=filters, center=CENTER,
        radius_km=3,
    )  # fmt: skip
    gateway = PyAirbnbSearchGateway(FixedClock(), client=client)
    summary = ScanMarketUseCase(gateway, repo, repo, FixedClock()).execute(command)[0]
    assert "price_min" not in client.search_kwargs and "price_max" not in client.search_kwargs
    assert (summary.counts.kept, summary.counts.filtered_out) == (2, 1)  # 100k, 150k kept; 300k out


def test_calendar_without_known_days_is_read_again() -> None:
    repo = InMemoryRepository()
    listing = ListingId("1001")
    repo.add(Snapshot(NOW, STAY, "COP", 2, (ListingOffer(make_listing("1001")),),
                      search_id="s", conditions_key=CONDITIONS.key))  # fmt: skip
    unknown = tuple(CalendarDay(date(2027, 1, d), None) for d in range(1, 10))
    repo.save_calendar(CalendarSnapshot(listing, NOW, unknown))  # stored before three states
    use_case = EnrichListingsUseCase(
        EnrichGateways(
            details=PyAirbnbDetailsGateway(client=SavedResponses()),
            calendars=PyAirbnbCalendarGateway(client=SavedResponses()),
            reviews=PyAirbnbReviewGateway(client=SavedResponses()),
        ),
        EnrichRepositories(snapshots=repo, profiles=repo, calendars=repo, reviews=repo),
        FixedClock(),
    )
    command = EnrichCommand(stays=[STAY], conditions_key=CONDITIONS.key,
                            parts=frozenset({EnrichPart.CALENDARS}))  # fmt: skip
    assert use_case.execute(command).fetched[EnrichPart.CALENDARS] == 1  # despite being fresh
    assert use_case.execute(command).fetched[EnrichPart.CALENDARS] == 0  # now it has known days


def test_availability_rate_and_coverage_share_one_base() -> None:
    known = CalendarSnapshot(
        ListingId("1"),
        NOW,
        tuple(CalendarDay((NOW + timedelta(days=d)).date(), d % 2 == 0) for d in range(10)),
    )
    unknown = CalendarSnapshot(ListingId("2"), NOW, tuple(
        CalendarDay((NOW + timedelta(days=d)).date(), None) for d in range(10)))  # fmt: skip
    summary = AvailabilitySummary.from_calendars([known, unknown], window_days=10)
    assert summary.listings_measured == 1 and summary.without_known_days == 1
    assert summary.median_unavailable_pct == 50.0 and summary.median_coverage_pct == 100.0
