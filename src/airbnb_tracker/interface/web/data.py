"""Loading market data for the pages, cached until the database changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import ReportBundle
from airbnb_tracker.application.use_cases import BuildReportQuery, ExportQuery, latest_run_stays
from airbnb_tracker.bootstrap import Container
from airbnb_tracker.domain.value_objects import Money
from airbnb_tracker.infrastructure.config import AppSettings, load_settings
from airbnb_tracker.infrastructure.exporters.dataset import (
    calendar_rows,
    listing_rows,
    pricing_rows,
    review_rows,
    search_run_rows,
)
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.formatting import format_money, money_input

_MY_PRICE = "_my_nightly_price"
EXCLUDE_OUTLIERS_KEY = "_exclude_outliers"


@dataclass(frozen=True)
class MarketData:
    """``tables`` feed the analysis (price outliers removed when excluded);
    ``raw_tables`` are exactly what was collected, for downloads and data quality."""

    bundle: ReportBundle
    tables: dict[str, pd.DataFrame]
    raw_tables: dict[str, pd.DataFrame]

    @property
    def outlier_ids(self) -> set[str]:
        return {str(flag.listing_id) for flag in self.bundle.outliers}

    @property
    def has_prices(self) -> bool:
        return bool(self.bundle.durations)

    @property
    def listings(self) -> pd.DataFrame:
        return self.tables["listings"]

    @property
    def analysed(self) -> pd.DataFrame:
        """Your listing plus the competitors whose page and calendar were read."""
        listings = self.listings
        if listings.empty:
            return listings
        return listings[listings["details_collected_at"].notna()]


def database_version(settings: AppSettings) -> float | None:
    path = settings.storage.database_path
    return path.stat().st_mtime if path.exists() else None


def last_update(settings: AppSettings) -> datetime | None:
    version = database_version(settings)
    return datetime.fromtimestamp(version, UTC) if version else None


def excluding_outliers() -> bool:
    return bool(st.session_state.get(EXCLUDE_OUTLIERS_KEY, True))


def outliers_toggle() -> None:
    """Global switch, shown in the sidebar: applies to every results page."""
    st.session_state.setdefault(EXCLUDE_OUTLIERS_KEY, True)
    st.toggle(
        "Exclude price outliers",
        key=EXCLUDE_OUTLIERS_KEY,
        help="Leaves implausible prices (blocking prices, prices far outside their "
        "segment) out of every statistic. They stay in the downloads. See Data quality.",
    )


def load_market(settings: AppSettings, my_price: float | None) -> MarketData | None:
    version = database_version(settings)
    if version is None:
        return None
    return _load(str(config_path()), version, my_price or 0.0, excluding_outliers())


@st.cache_data(show_spinner="Loading your market data…")
def _load(config: str, db_version: float, my_price: float, exclude_outliers: bool) -> MarketData:
    del db_version  # cache key only: data reloads when the database changes
    settings = load_settings(Path(config))
    container = Container(settings)
    now = datetime.now(UTC)
    my_id = settings.my_listing.listing_id
    bundle = container.build_report.execute(
        BuildReportQuery(
            stays=latest_run_stays(container.repository) or settings.stays.resolve(now.date()),
            my_listing_id=my_id,
            my_nightly_price=(
                Money(Decimal(str(my_price)), settings.scraping.currency) if my_price else None
            ),
            availability_window_days=settings.enrich.availability_window_days,
            exclude_outliers=exclude_outliers,
        )
    )
    dataset = container.export_dataset.build(
        ExportQuery(
            as_of=now,
            my_listing_id=my_id,
            my_location=settings.search.center,
            min_nights_window_days=settings.enrich.availability_window_days,
        )
    )
    raw = {
        "listings": pd.DataFrame(listing_rows(dataset.listings)),
        "pricing_snapshots": pd.DataFrame(pricing_rows(dataset.snapshots)),
        "calendar_snapshots": pd.DataFrame(calendar_rows(dataset.calendars)),
        "reviews": pd.DataFrame(review_rows(dataset.reviews)),
        "search_runs": pd.DataFrame(search_run_rows(dataset.search_runs)),
    }
    tables = dict(raw)
    flagged = {str(flag.listing_id) for flag in bundle.outliers}
    pricing = raw["pricing_snapshots"]
    if exclude_outliers and flagged and not pricing.empty:
        tables["pricing_snapshots"] = pricing[~pricing["listing_id"].isin(flagged)]
    return MarketData(bundle=bundle, tables=tables, raw_tables=raw)


def my_price_input(settings: AppSettings) -> int | None:
    """Your nightly price, shared by every page that shows a position."""
    configured = settings.my_listing.nightly_price
    return money_input(
        "Your nightly price",
        key=_MY_PRICE,
        currency=settings.scraping.currency,
        default=round(configured) if configured else None,
        help="Type the price you charge (or plan to charge) per night to see where it "
        "stands. It is not saved; set a default in step 1.",
    )


def money(amount: float | None, currency: str) -> str:
    return format_money(amount, currency)


def ago(moment: datetime | None) -> str:
    if moment is None:
        return "never"
    minutes = int((datetime.now(UTC) - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // (24 * 60)} days ago"
