"""Dataset exporters (Strategy): the same tables as CSV or JSON files.

listings.<ext>            one row per listing (yours included, ``is_mine``)
pricing_snapshots.<ext>   one row per listing x search (full history)
calendar_snapshots.<ext>  one row per listing x observed day (full history)
reviews.<ext>             one row per review: date, rating, language (no personal data)
search_runs.<ext>         one row per search: parameters and coverage
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from airbnb_tracker.application.dto import Dataset, ListingRecord, SearchRun
from airbnb_tracker.domain.entities import Snapshot
from airbnb_tracker.domain.profile import CalendarSnapshot, Review
from airbnb_tracker.domain.value_objects import Money

LIST_SEPARATOR = " | "


def listing_rows(records: Sequence[ListingRecord]) -> Iterator[dict[str, Any]]:
    for record in records:
        listing, profile = record.listing, record.profile
        yield {
            "listing_id": str(listing.id),
            "is_mine": record.is_mine,
            "url": listing.url,
            "title": listing.name,
            "description": profile.description if profile else None,
            "latitude": listing.location.latitude if listing.location else None,
            "longitude": listing.location.longitude if listing.location else None,
            "distance_km": record.distance_km,
            "property_type": listing.property_type,
            "room_type": listing.room_type.value,
            "bedrooms": listing.bedrooms,
            "bathrooms": listing.bathrooms,
            "beds": listing.beds,
            "max_guests": profile.person_capacity if profile else None,
            "rating": listing.rating,
            "review_count": listing.review_count,
            "minimum_nights": record.minimum_nights,
            "is_superhost": profile.is_superhost if profile else None,
            "is_guest_favorite": listing.is_guest_favorite,
            "amenities": sorted(a.name for a in profile.amenities) if profile else None,
            "photos": list(listing.photos),
            "last_review_at": _iso(record.last_review_at),
            "reviews_last_90_days": record.reviews_last_90_days,
            "details_collected_at": _iso(profile.captured_at if profile else None),
        }


def pricing_rows(snapshots: Sequence[Snapshot]) -> Iterator[dict[str, Any]]:
    """``total_price`` is the stay price after discounts, before cleaning fee and taxes."""
    for snapshot in snapshots:
        for position, offer in enumerate(snapshot.offers, start=1):
            quote = offer.quote
            yield {
                "listing_id": str(offer.listing.id),
                "collected_at": snapshot.captured_at.isoformat(),
                "check_in": snapshot.stay.check_in.isoformat(),
                "check_out": snapshot.stay.check_out.isoformat(),
                "nights": snapshot.stay.nights,
                "guests": snapshot.guests,
                "currency": snapshot.currency,
                "original_price": _amount(quote.original_total or quote.total if quote else None),
                "total_price": _amount(quote.total if quote else None),
                "nightly_price": _amount(quote.nightly if quote else None),
                "discount_pct": (
                    float(quote.discount_pct) if quote and quote.discount_pct else None
                ),
                "is_available": offer.is_available,
                "search_position": position,
            }


def calendar_rows(calendars: Sequence[CalendarSnapshot]) -> Iterator[dict[str, Any]]:
    for calendar in calendars:
        for day in calendar.days:
            yield {
                "listing_id": str(calendar.listing_id),
                "collected_at": calendar.captured_at.isoformat(),
                "date": day.day.isoformat(),
                "available": day.available,
                "minimum_nights": day.min_nights,
            }


def review_rows(reviews: Sequence[Review]) -> Iterator[dict[str, Any]]:
    for review in reviews:
        yield {
            "listing_id": str(review.listing_id),
            "review_id": review.review_id,
            "created_at": review.created_at.isoformat(),
            "rating": review.rating,
            "language": review.language,
        }


def search_run_rows(runs: Sequence[SearchRun]) -> Iterator[dict[str, Any]]:
    for run in runs:
        filters = run.filters
        yield {
            "run_id": run.run_id,
            "started_at": run.started_at.isoformat(),
            "duration_seconds": round((run.finished_at - run.started_at).total_seconds(), 1),
            "check_in": run.stay.check_in.isoformat(),
            "check_out": run.stay.check_out.isoformat(),
            "nights": run.stay.nights,
            "guests": run.guests,
            "currency": run.currency,
            "room_type": filters.room_type.value if filters.room_type else None,
            "min_bedrooms": filters.min_bedrooms,
            "min_bathrooms": filters.min_bathrooms,
            "min_beds": filters.min_beds,
            "price_min": filters.price_min,
            "price_max": filters.price_max,
            "latitude": run.center.latitude if run.center else None,
            "longitude": run.center.longitude if run.center else None,
            "radius_km": run.radius_km,
            "url": run.url,
            "complete_coverage": run.complete_coverage,
            "results_found": run.results_found,
            "results_kept": run.results_kept,
            "truncated": run.truncated,
            "error": run.error,
        }


def _tables(dataset: Dataset) -> Mapping[str, Iterator[dict[str, Any]]]:
    return {
        "listings": listing_rows(dataset.listings),
        "pricing_snapshots": pricing_rows(dataset.snapshots),
        "calendar_snapshots": calendar_rows(dataset.calendars),
        "reviews": review_rows(dataset.reviews),
        "search_runs": search_run_rows(dataset.search_runs),
    }


class CsvDatasetExporter:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def export(self, dataset: Dataset) -> str:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in _tables(dataset).items():
            path = self._output_dir / f"{name}.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer: csv.DictWriter[str] | None = None
                for row in rows:
                    flat = {k: _flatten(v) for k, v in row.items()}
                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=list(flat))
                        writer.writeheader()
                    writer.writerow(flat)
        return str(self._output_dir)


class JsonDatasetExporter:
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    def export(self, dataset: Dataset) -> str:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in _tables(dataset).items():
            path = self._output_dir / f"{name}.json"
            path.write_text(json.dumps(list(rows), ensure_ascii=False, indent=1), "utf-8")
        return str(self._output_dir)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _flatten(value: Any) -> Any:
    return LIST_SEPARATOR.join(value) if isinstance(value, list) else value


def _amount(money: Money | None) -> float | None:
    return float(money.amount) if money else None
