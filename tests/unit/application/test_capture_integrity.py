"""P0 data integrity: what each price means, which search it came from, and how a
failed search affects the reference used by the dashboard."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import CENTER, NOW, FixedClock

from airbnb_tracker.application.dto import (
    FailureKind,
    RowCounts,
    SearchConditions,
    SearchCriteria,
    SearchResult,
    SearchStatus,
    classify_search,
)
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.use_cases import (
    BuildMarketReportUseCase,
    BuildReportQuery,
    ScanMarketCommand,
    ScanMarketUseCase,
)
from airbnb_tracker.application.use_cases.scan_market import ScanSummary
from airbnb_tracker.domain.entities import Snapshot
from airbnb_tracker.domain.price_reading import Exclusion, PriceUnit
from airbnb_tracker.domain.profile import CalendarDay, CalendarSnapshot
from airbnb_tracker.domain.value_objects import ListingId, StayPeriod
from airbnb_tracker.infrastructure.airbnb.mappers import SearchResultMapper
from airbnb_tracker.infrastructure.airbnb.text_parsing import parse_qualifier
from airbnb_tracker.infrastructure.persistence import InMemoryRepository, SqliteRepository

FIXTURE = Path(__file__).parents[2] / "fixtures" / "search_result.json"
STAY = StayPeriod(date(2027, 1, 15), date(2027, 1, 22))  # 7 nights


def raw_result(room_id: str, amount: float, qualifier: str | None) -> dict[str, Any]:
    """The saved Airbnb search result, with another id, price and qualifier."""
    raw: dict[str, Any] = copy.deepcopy(json.loads(FIXTURE.read_text()))
    raw["room_id"] = room_id
    raw["coordinates"] = {"latitude": CENTER.latitude, "longitud": CENTER.longitude}
    raw["price"]["unit"] = {"qualifier": qualifier, "amount": amount, "discount": None}
    return raw


def conditions(guests: int) -> SearchConditions:
    return SearchConditions.build(
        guests=guests, currency="COP", center=CENTER, radius_km=3, url=None,
        filters={}, kept_listings=None, complete_coverage=True,
    )  # fmt: skip


# --- P0-1: what a price represents ---------------------------------------------------


@pytest.mark.parametrize(
    ("qualifier", "unit", "nights"),
    [
        ("for 7 nights", PriceUnit.STAY, 7),
        ("night", PriceUnit.NIGHT, None),
        ("weekly", PriceUnit.WEEK, None),
        ("monthly", PriceUnit.MONTH, None),
        ("", PriceUnit.UNKNOWN, None),
        (None, PriceUnit.UNKNOWN, None),
        ("total before taxes", PriceUnit.UNKNOWN, None),
        ("7 nights", PriceUnit.UNKNOWN, None),
    ],
)
def test_qualifier_is_read_only_when_explicit(
    qualifier: str | None, unit: PriceUnit, nights: int | None
) -> None:
    parsed = parse_qualifier(qualifier)
    assert (parsed.unit, parsed.stated_nights) == (unit, nights)


@pytest.mark.parametrize(
    ("qualifier", "amount", "nightly"),
    [
        ("for 7 nights", 700_000, Decimal("100000")),  # stay total
        ("night", 100_000, Decimal("100000")),
        ("weekly", 700_000, Decimal("100000")),
        ("monthly", 3_000_000, Decimal("100000")),  # Airbnb months are 30 nights
    ],
)
def test_same_amount_is_normalised_by_its_unit(
    qualifier: str, amount: int, nightly: Decimal
) -> None:
    offer = SearchResultMapper("COP").to_offer(raw_result("1", amount, qualifier), STAY, NOW)
    assert offer is not None and offer.quote is not None
    assert offer.quote.nightly.amount == nightly
    assert offer.quote.reading is not None
    assert offer.quote.reading.displayed.text == qualifier


@pytest.mark.parametrize(
    ("qualifier", "reason"),
    [
        ("", Exclusion.UNKNOWN_UNIT),
        ("total", Exclusion.UNKNOWN_UNIT),
        ("for 5 nights", Exclusion.NIGHTS_MISMATCH),  # does not match the 7-night search
    ],
)
def test_ambiguous_price_is_kept_with_its_reason_but_not_compared(
    qualifier: str, reason: Exclusion
) -> None:
    offer = SearchResultMapper("COP").to_offer(raw_result("1", 500_000, qualifier), STAY, NOW)
    assert offer is not None
    assert offer.quote is None and offer.is_available
    assert offer.rejected is not None and offer.rejected.exclusion is reason
    assert offer.rejected.displayed.amount == Decimal("500000")


# --- P0-3: valid, empty, partial and failed searches ---------------------------------


@pytest.mark.parametrize(
    ("counts", "truncated", "error", "expected"),
    [
        (RowCounts(10, 10, 8, 8, 0), False, None, (SearchStatus.COMPLETE, None)),
        (RowCounts(10, 9, 8, 8, 0), False, None, (SearchStatus.PARTIAL, None)),
        (RowCounts(10, 10, 8, 8, 0), True, None, (SearchStatus.PARTIAL, None)),
        (RowCounts(0, 0, 0, 0, 0), False, None, (SearchStatus.EMPTY, None)),
        (RowCounts(), False, "timeout", (SearchStatus.FAILED, FailureKind.REQUEST)),
        (RowCounts(10, 0, 0, 0, 0), False, None, (SearchStatus.FAILED, FailureKind.FORMAT)),
        (RowCounts(10, 10, 8, 0, 8), False, None, (SearchStatus.FAILED, FailureKind.PRICES)),
    ],
)
def test_search_status(
    counts: RowCounts,
    truncated: bool,
    error: str | None,
    expected: tuple[SearchStatus, FailureKind | None],
) -> None:
    assert classify_search(counts, truncated=truncated, error=error) == expected


# --- P0-2: captures linked to their search conditions --------------------------------


class ScriptedGateway:
    """Answers each search with the next scripted response (a result or an error)."""

    def __init__(self) -> None:
        self.responses: list[Callable[[SearchCriteria], SearchResult]] = []

    def search(self, criteria: SearchCriteria) -> SearchResult:
        return self.responses.pop(0)(criteria)


def prices(*nightly: int) -> Callable[[SearchCriteria], SearchResult]:
    def respond(criteria: SearchCriteria) -> SearchResult:
        mapper = SearchResultMapper(criteria.currency)
        offers = [
            mapper.to_offer(raw_result(str(i + 1), n * 7, "for 7 nights"), criteria.stay, NOW)
            for i, n in enumerate(nightly)
        ]
        return SearchResult([o for o in offers if o], received=len(offers))

    return respond


def network_error(_criteria: SearchCriteria) -> SearchResult:
    raise GatewayError("Airbnb search failed: connection reset")


def test_dashboard_reuses_only_the_history_of_matching_conditions() -> None:
    repo, clock, gateway = SqliteRepository(":memory:"), FixedClock(), ScriptedGateway()
    ids = iter(f"search-{n}" for n in range(100))
    scan = ScanMarketUseCase(gateway, repo, repo, clock, id_factory=lambda: next(ids))
    report = BuildMarketReportUseCase(repo, repo, clock)

    def run(guests: int, response: Callable[[SearchCriteria], SearchResult]) -> ScanSummary:
        gateway.responses.append(response)
        clock.advance(hours=1)
        command = ScanMarketCommand(
            stays=[STAY], currency="COP", conditions=conditions(guests), center=CENTER,
            radius_km=3,
        )  # fmt: skip
        return scan.execute(command)[0]

    two_guests = run(2, prices(100_000, 120_000))
    captured_two_guests = clock.now()
    four_guests = run(4, prices(200_000, 220_000))
    failed = run(2, network_error)  # same dates and conditions as the first search

    assert two_guests.status is SearchStatus.COMPLETE
    assert failed.status is SearchStatus.FAILED and failed.failure is FailureKind.REQUEST

    reference = repo.latest(STAY, conditions(2).key)
    assert reference is not None
    assert reference.search_id == two_guests.search_id  # shows which search it came from
    assert reference.captured_at == captured_two_guests  # ...and when
    assert repo.latest(STAY, conditions(4).key).search_id == four_guests.search_id  # type: ignore[union-attr]

    for guests, median in ((2, 110_000), (4, 210_000)):
        bundle = report.execute(
            BuildReportQuery(stays=[STAY], conditions_key=conditions(guests).key)
        )
        distribution = bundle.durations[0].distribution
        assert distribution is not None and distribution.median.amount == median

    runs = {run.search_id: run for run in repo.all_runs()}
    assert runs[failed.search_id].status is SearchStatus.FAILED
    assert runs[two_guests.search_id].counts.priced == 2


def test_conditions_key_ignores_dates_but_not_guests_currency_area_or_filters() -> None:
    base = conditions(2)
    assert base.key == conditions(2).key
    assert base.key != conditions(4).key
    assert base.key != SearchConditions.build(
        guests=2, currency="USD", center=CENTER, radius_km=3, url=None, filters={},
        kept_listings=None, complete_coverage=True,
    ).key  # fmt: skip
    assert base.key != SearchConditions.build(
        guests=2, currency="COP", center=CENTER, radius_km=3, url=None,
        filters={"min_bedrooms": 2}, kept_listings=None, complete_coverage=True,
    ).key  # fmt: skip
    url = "https://www.airbnb.com/s/Pereira/homes?checkin=2027-01-01&checkout=2027-01-08&adults=2"
    later = url.replace("2027-01-01", "2027-03-01").replace("2027-01-08", "2027-03-08")
    by_url = {
        SearchConditions.build(
            guests=2, currency="COP", center=None, radius_km=None, url=u, filters={},
            kept_listings=None, complete_coverage=False,
        ).key
        for u in (url, later)
    }  # fmt: skip
    assert len(by_url) == 1


def test_older_captures_without_units_never_enter_statistics(tmp_path: Path) -> None:
    """Rows stored before this change: kept and exported, never compared."""
    database = tmp_path / "old.db"
    repo = SqliteRepository(database)
    with repo._connection() as connection:
        connection.execute(
            "INSERT INTO snapshots (captured_at, check_in, check_out, currency, guests, "
            "conditions_key) VALUES (?, ?, ?, 'COP', 2, ?)",
            (NOW.isoformat(), STAY.check_in.isoformat(), STAY.check_out.isoformat(), "k"),
        )
        connection.execute(
            "INSERT INTO offers (snapshot_id, search_position, listing_id, name, room_type, "
            "property_type, review_count, is_guest_favorite, photos, total) "
            "VALUES (1, 1, '1', 'Old', 'entire_place', 'Apartment', 0, 0, '[]', '700000')"
        )
    snapshot = SqliteRepository(database).latest(STAY, "k")
    assert snapshot is not None
    offer = snapshot.offers[0]
    assert offer.quote is None
    assert offer.rejected is not None and offer.rejected.exclusion is Exclusion.NOT_RECORDED


def test_availability_only_counts_listings_of_the_current_sample() -> None:
    """Calendars of listings seen under other conditions never weigh in."""
    repo, clock = InMemoryRepository(), FixedClock()
    key = conditions(2).key
    offer = SearchResultMapper("COP").to_offer(raw_result("1", 700_000, "for 7 nights"), STAY, NOW)
    assert offer is not None
    repo.add(Snapshot(NOW, STAY, "COP", 2, (offer,), search_id="s", conditions_key=key))
    days = [(NOW + timedelta(days=d)).date() for d in range(10)]
    repo.save_calendar(
        CalendarSnapshot(ListingId("1"), NOW, tuple(CalendarDay(d, True) for d in days))
    )
    repo.save_calendar(
        CalendarSnapshot(ListingId("99"), NOW, tuple(CalendarDay(d, False) for d in days))
    )
    bundle = BuildMarketReportUseCase(repo, repo, clock).execute(
        BuildReportQuery(stays=[STAY], conditions_key=key, availability_window_days=10)
    )
    assert bundle.availability.listings_measured == 1
    assert bundle.availability.median_unavailable_pct == 0.0
