"""Adapters (GoF *Adapter*) from the pyairbnb library to the application ports."""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pyairbnb
from pyairbnb import SearchResultsTruncatedWarning

from airbnb_tracker.application.dto import ListingDetails, SearchCriteria, SearchResult
from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.application.ports import Clock
from airbnb_tracker.domain.entities import RoomType
from airbnb_tracker.domain.profile import CalendarDay, Review
from airbnb_tracker.domain.value_objects import BoundingBox, ListingId, StayPeriod
from airbnb_tracker.infrastructure.airbnb.mappers import (
    CalendarMapper,
    DetailsMapper,
    ReviewMapper,
    SearchResultMapper,
)

# Text parsing ("for 3 nights", "2 bedrooms") relies on English responses.
LANGUAGE = "en"
_PLACE_TYPE_FILTERS = {
    RoomType.ENTIRE_PLACE: "Entire home/apt",
    RoomType.PRIVATE_ROOM: "Private room",
}
_MIN_ZOOM, _MAX_ZOOM = 10, 18


RawResults = list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class RawSearch:
    results: RawResults
    truncated: bool = False


class PyAirbnbClient(Protocol):
    """The slice of pyairbnb this project uses; tests inject a fake implementation."""

    def search_all(self, **kwargs: Any) -> RawSearch: ...

    def search_all_from_url(self, url: str, currency: str, proxy_url: str) -> RawSearch: ...

    def get_reviews(self, room_url: str, proxy_url: str) -> RawResults: ...

    def get_metadata(self, room_url: str, proxy_url: str) -> dict[str, Any]: ...

    def get_calendar(self, room_id: str, proxy_url: str) -> list[dict[str, Any]]: ...


class DefaultPyAirbnbClient:
    """Delegates to the real pyairbnb module, reusing one API key per process."""

    def __init__(self) -> None:
        self._api_key: str | None = None

    def search_all(self, **kwargs: Any) -> RawSearch:
        return _watch_truncation(lambda: pyairbnb.search_all(**kwargs))

    def search_all_from_url(self, url: str, currency: str, proxy_url: str) -> RawSearch:
        return _watch_truncation(
            lambda: pyairbnb.search_all_from_url(
                url, currency=currency, language=LANGUAGE, proxy_url=proxy_url
            )
        )

    def get_reviews(self, room_url: str, proxy_url: str) -> RawResults:
        return list(pyairbnb.get_reviews(room_url, LANGUAGE, proxy_url))

    def get_metadata(self, room_url: str, proxy_url: str) -> dict[str, Any]:
        data, _price_input, _cookies = pyairbnb.get_metadata_from_url(room_url, LANGUAGE, proxy_url)
        return dict(data)

    def get_calendar(self, room_id: str, proxy_url: str) -> list[dict[str, Any]]:
        if self._api_key is None:
            self._api_key = str(pyairbnb.get_api_key(proxy_url))
        return list(
            pyairbnb.get_calendar(
                api_key=self._api_key,
                room_id=room_id,
                proxy_url=proxy_url,
                return_dataclass=False,
            )
        )


class PyAirbnbSearchGateway:
    def __init__(
        self,
        clock: Clock,
        *,
        proxy_url: str = "",
        complete_coverage: bool = True,
        client: PyAirbnbClient | None = None,
    ) -> None:
        self._clock = clock
        self._proxy_url = proxy_url
        # Airbnb caps result pages; complete coverage splits the area into smaller boxes.
        self._complete_coverage = complete_coverage
        self._client = client or DefaultPyAirbnbClient()

    def search(self, criteria: SearchCriteria) -> SearchResult:
        try:
            if criteria.template_url:
                raw = self._search_by_url(criteria.template_url, criteria)
            elif criteria.area:
                raw = self._search_by_area(criteria.area, criteria)
            else:  # pragma: no cover - SearchCriteria forbids it
                raise ValueError("criteria without area or template_url")
        except Exception as error:
            raise GatewayError(f"Airbnb search failed: {error}") from error

        mapper = SearchResultMapper(criteria.currency, criteria.filters.room_type)
        captured_at = self._clock.now()
        rows = list(raw.results)
        offers = (mapper.to_offer(item, criteria.stay, captured_at) for item in rows)
        return SearchResult(
            [o for o in offers if o is not None], truncated=raw.truncated, received=len(rows)
        )

    def _search_by_url(self, template_url: str, criteria: SearchCriteria) -> RawSearch:
        url = with_stay(template_url, criteria.stay, criteria.filters.guests)
        return self._client.search_all_from_url(url, criteria.currency, self._proxy_url)

    def _search_by_area(self, area: BoundingBox, criteria: SearchCriteria) -> RawSearch:
        stay, filters = criteria.stay, criteria.filters
        return self._client.search_all(
            check_in=stay.check_in.isoformat(),
            check_out=stay.check_out.isoformat(),
            ne_lat=area.north_east.latitude,
            ne_long=area.north_east.longitude,
            sw_lat=area.south_west.latitude,
            sw_long=area.south_west.longitude,
            zoom_value=zoom_for(area),
            adults=filters.guests,
            currency=criteria.currency,
            language=LANGUAGE,
            proxy_url=self._proxy_url,
            place_type=_PLACE_TYPE_FILTERS.get(filters.room_type) if filters.room_type else None,
            min_bedrooms=filters.min_bedrooms,
            min_beds=filters.min_beds,
            min_bathrooms=filters.min_bathrooms,
            price_min=filters.price_min,
            price_max=filters.price_max,
            complete=self._complete_coverage,
        )


class PyAirbnbDetailsGateway:
    def __init__(
        self,
        *,
        proxy_url: str = "",
        client: PyAirbnbClient | None = None,
        mapper: DetailsMapper | None = None,
    ) -> None:
        self._proxy_url = proxy_url
        self._client = client or DefaultPyAirbnbClient()
        self._mapper = mapper or DetailsMapper()

    def get_details(self, listing_id: ListingId) -> ListingDetails:
        try:
            data = self._client.get_metadata(
                f"https://www.airbnb.com/rooms/{listing_id}", self._proxy_url
            )
            return self._mapper.to_details(listing_id, data)
        except Exception as error:
            raise GatewayError(f"Could not load listing {listing_id}: {error}") from error


class PyAirbnbCalendarGateway:
    def __init__(
        self,
        *,
        proxy_url: str = "",
        client: PyAirbnbClient | None = None,
        mapper: CalendarMapper | None = None,
    ) -> None:
        self._proxy_url = proxy_url
        self._client = client or DefaultPyAirbnbClient()
        self._mapper = mapper or CalendarMapper()

    def get_calendar(self, listing_id: ListingId) -> list[CalendarDay]:
        try:
            months = self._client.get_calendar(str(listing_id), self._proxy_url)
            return self._mapper.to_days(months)
        except Exception as error:
            raise GatewayError(f"Could not load calendar of {listing_id}: {error}") from error


class PyAirbnbReviewGateway:
    def __init__(
        self,
        *,
        proxy_url: str = "",
        client: PyAirbnbClient | None = None,
        mapper: ReviewMapper | None = None,
    ) -> None:
        self._proxy_url = proxy_url
        self._client = client or DefaultPyAirbnbClient()
        self._mapper = mapper or ReviewMapper()

    def get_reviews(self, listing_id: ListingId) -> list[Review]:
        try:
            raw = self._client.get_reviews(
                f"https://www.airbnb.com/rooms/{listing_id}", self._proxy_url
            )
            return self._mapper.to_reviews(listing_id, raw)
        except Exception as error:
            raise GatewayError(f"Could not load reviews of {listing_id}: {error}") from error


def _watch_truncation(search: Callable[[], Iterable[dict[str, Any]]]) -> RawSearch:
    """Run a pyairbnb search, turning its page-cap warning into a flag."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = list(search())
    truncated = any(issubclass(w.category, SearchResultsTruncatedWarning) for w in caught)
    return RawSearch(results, truncated)


def with_stay(url: str, stay: StayPeriod, guests: int) -> str:
    """Return the search URL with the stay dates set (and guests, if the URL has none)."""
    parts = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"checkin", "checkout"}]
    params += [("checkin", stay.check_in.isoformat()), ("checkout", stay.check_out.isoformat())]
    if not any(key == "adults" for key, _ in params):
        params.append(("adults", str(guests)))
    return urlunsplit(parts._replace(query=urlencode(params)))


def zoom_for(area: BoundingBox) -> int:
    """Map zoom level whose viewport roughly matches the search box."""
    span = max(area.span_degrees, 1e-6)
    return max(_MIN_ZOOM, min(_MAX_ZOOM, round(math.log2(360 / span))))
