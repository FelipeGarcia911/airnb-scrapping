"""Load and validate ``config.yaml`` into typed, immutable settings."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypeVar

import yaml

from airbnb_tracker.application.dto import SearchFilters
from airbnb_tracker.domain.entities import RoomType
from airbnb_tracker.domain.exceptions import DomainError
from airbnb_tracker.domain.stay_planner import plan_stays
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, StayPeriod

T = TypeVar("T")

PROXY_ENV_VAR = "AIRBNB_TRACKER_PROXY_URL"
DEFAULT_DURATIONS = (7, 14, 20, 28, 30, 60, 90)


class ConfigurationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MyListingSettings:
    listing_id: ListingId | None = None
    nightly_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SearchSettings:
    center: GeoPoint | None = None
    radius_km: float = 5.0
    url: str | None = None
    complete_coverage: bool = True
    max_listings: int | None = None
    filters: SearchFilters = field(default_factory=SearchFilters)
    max_bedrooms: int | None = None
    min_rating: float | None = None
    exclude_ids: tuple[ListingId, ...] = ()


@dataclass(frozen=True, slots=True)
class StaySettings:
    durations: tuple[int, ...] = DEFAULT_DURATIONS
    first_check_in_days: int = 7
    every_days: int = 30
    horizon_days: int = 180

    def resolve(self, today: date) -> list[StayPeriod]:
        return plan_stays(
            today,
            self.durations,
            first_check_in_days=self.first_check_in_days,
            every_days=self.every_days,
            horizon_days=self.horizon_days,
        )


@dataclass(frozen=True, slots=True)
class EnrichSettings:
    competitors: int | None = 50
    max_listings_per_run: int = 50
    details_refresh: timedelta = timedelta(days=7)
    calendar_refresh: timedelta = timedelta(days=2)
    reviews_refresh: timedelta = timedelta(days=7)
    availability_window_days: int = 60


@dataclass(frozen=True, slots=True)
class ScrapingSettings:
    currency: str = "COP"
    proxy_url: str = ""
    min_delay_seconds: float = 3.0
    max_attempts: int = 3
    backoff_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class StorageSettings:
    database_path: Path = Path("data/airbnb.db")
    exports_dir: Path = Path("exports")


@dataclass(frozen=True, slots=True)
class AppSettings:
    my_listing: MyListingSettings
    search: SearchSettings
    stays: StaySettings
    enrich: EnrichSettings
    scraping: ScrapingSettings
    storage: StorageSettings


def load_settings(path: Path) -> AppSettings:
    if not path.is_file():
        raise ConfigurationError(
            f"{path} not found. Copy config.example.yaml to config.yaml and edit it."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(raw, Mapping):
        raise ConfigurationError("The configuration root must be a mapping")
    try:
        return AppSettings(
            my_listing=_parse_my_listing(_section(raw, "my_listing")),
            search=_parse_search(_section(raw, "search")),
            stays=_parse_stays(_section(raw, "stays")),
            enrich=_parse_enrich(_section(raw, "enrich")),
            scraping=_parse_scraping(_section(raw, "scraping")),
            storage=_parse_storage(_section(raw, "storage"), path.resolve().parent),
        )
    except (DomainError, ValueError, TypeError, KeyError, InvalidOperation) as error:
        raise ConfigurationError(f"Invalid configuration: {error}") from error


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name) or {}
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"Section `{name}` must be a mapping")
    return value


def _parse_my_listing(raw: Mapping[str, Any]) -> MyListingSettings:
    return MyListingSettings(
        listing_id=ListingId(str(raw["id"])) if raw.get("id") else None,
        nightly_price=_opt(Decimal, raw.get("nightly_price")),
    )


def _parse_search(raw: Mapping[str, Any]) -> SearchSettings:
    latitude, longitude, url = raw.get("latitude"), raw.get("longitude"), raw.get("url")
    if url is None and (latitude is None or longitude is None):
        raise ConfigurationError("search needs `latitude` and `longitude`, or a `url`")
    radius = float(raw.get("radius_km", 5.0))
    guests = int(raw.get("guests", 2))
    if radius <= 0 or guests < 1:
        raise ConfigurationError("search.radius_km must be > 0 and search.guests >= 1")
    room_type = raw.get("room_type")
    return SearchSettings(
        center=(
            GeoPoint(float(latitude), float(longitude))
            if latitude is not None and longitude is not None
            else None
        ),
        radius_km=radius,
        url=str(url) if url else None,
        complete_coverage=bool(raw.get("complete_coverage", True)),
        max_listings=_opt(int, raw.get("max_listings")),
        filters=SearchFilters(
            guests=guests,
            room_type=RoomType(room_type) if room_type else None,
            min_bedrooms=_opt(int, raw.get("min_bedrooms")),
            min_beds=_opt(int, raw.get("min_beds")),
            min_bathrooms=_opt(int, raw.get("min_bathrooms")),
            price_min=_opt(int, raw.get("price_min")),
            price_max=_opt(int, raw.get("price_max")),
        ),
        max_bedrooms=_opt(int, raw.get("max_bedrooms")),
        min_rating=_opt(float, raw.get("min_rating")),
        exclude_ids=tuple(ListingId(str(v)) for v in raw.get("exclude_ids") or []),
    )


def _parse_stays(raw: Mapping[str, Any]) -> StaySettings:
    defaults = StaySettings()
    durations = tuple(int(n) for n in raw.get("durations") or defaults.durations)
    if not durations or min(durations) < 1:
        raise ConfigurationError("stays.durations must contain nights >= 1")
    return StaySettings(
        durations=durations,
        first_check_in_days=int(raw.get("first_check_in_days", defaults.first_check_in_days)),
        every_days=int(raw.get("every_days", defaults.every_days)),
        horizon_days=int(raw.get("horizon_days", defaults.horizon_days)),
    )


def _parse_enrich(raw: Mapping[str, Any]) -> EnrichSettings:
    defaults = EnrichSettings()
    return EnrichSettings(
        competitors=_opt(int, raw.get("competitors", defaults.competitors)),
        max_listings_per_run=int(raw.get("max_listings_per_run", defaults.max_listings_per_run)),
        details_refresh=timedelta(days=float(raw.get("details_refresh_days", 7))),
        calendar_refresh=timedelta(days=float(raw.get("calendar_refresh_days", 2))),
        reviews_refresh=timedelta(days=float(raw.get("reviews_refresh_days", 7))),
        availability_window_days=int(
            raw.get("availability_window_days", defaults.availability_window_days)
        ),
    )


def _parse_scraping(raw: Mapping[str, Any]) -> ScrapingSettings:
    defaults = ScrapingSettings()
    return ScrapingSettings(
        currency=str(raw.get("currency", defaults.currency)).upper(),
        proxy_url=os.environ.get(PROXY_ENV_VAR) or str(raw.get("proxy_url") or ""),
        min_delay_seconds=float(raw.get("min_delay_seconds", defaults.min_delay_seconds)),
        max_attempts=int(raw.get("max_attempts", defaults.max_attempts)),
        backoff_seconds=float(raw.get("backoff_seconds", defaults.backoff_seconds)),
    )


def _parse_storage(raw: Mapping[str, Any], base_dir: Path) -> StorageSettings:
    defaults = StorageSettings()

    def resolve(value: Any, default: Path) -> Path:
        path = Path(value) if value else default
        return path if path.is_absolute() else base_dir / path

    return StorageSettings(
        database_path=resolve(raw.get("database_path"), defaults.database_path),
        exports_dir=resolve(raw.get("exports_dir"), defaults.exports_dir),
    )


def _opt(cast: Callable[[Any], T], value: Any) -> T | None:
    return None if value is None else cast(value)
