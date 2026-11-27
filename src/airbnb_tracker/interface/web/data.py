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
from airbnb_tracker.domain.outliers import OutlierPolicy, OutlierSensitivity
from airbnb_tracker.domain.property_types import PropertyGroup
from airbnb_tracker.domain.value_objects import Money
from airbnb_tracker.infrastructure.config import AppSettings, load_settings
from airbnb_tracker.infrastructure.exporters.dataset import (
    calendar_rows,
    listing_rows,
    pricing_rows,
    review_rows,
    search_run_rows,
)
from airbnb_tracker.infrastructure.persistence import SqliteRepository
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.formatting import format_money, money_input
from airbnb_tracker.interface.web.i18n import t

_MY_PRICE = "_my_nightly_price"
EXCLUDE_OUTLIERS_KEY = "_exclude_outliers"
SENSITIVITY_KEY = "_outlier_sensitivity"


@dataclass(frozen=True)
class MarketData:
    """Three views of the same data:

    * ``raw_tables``: exactly what was collected (downloads);
    * ``comp_set_tables``: only comparable property kinds, outliers included (data quality);
    * ``tables``: the comp set without price outliers when excluded (every analysis).
    """

    bundle: ReportBundle
    tables: dict[str, pd.DataFrame]
    raw_tables: dict[str, pd.DataFrame]
    comp_set_tables: dict[str, pd.DataFrame]

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


def excluding_outliers(settings: AppSettings | None = None) -> bool:
    default = settings.analysis.exclude_outliers if settings else True
    return bool(st.session_state.get(EXCLUDE_OUTLIERS_KEY, default))


def outlier_sensitivity(settings: AppSettings | None = None) -> OutlierSensitivity:
    default = settings.analysis.outlier_sensitivity if settings else OutlierSensitivity.STRICT
    return OutlierSensitivity(st.session_state.get(SENSITIVITY_KEY, default))


def analysis_controls(settings: AppSettings | None) -> None:
    """Outlier switches. The chosen values are kept outside the widgets, so they still
    apply on every other page (Streamlit forgets widget state when you leave a page)."""

    def remember(widget_key: str, key: str) -> None:
        st.session_state[key] = st.session_state[widget_key]

    exclude = st.toggle(
        t("Exclude price outliers"),
        value=excluding_outliers(settings),
        key="_w_exclude_outliers",
        on_change=remember,
        args=("_w_exclude_outliers", EXCLUDE_OUTLIERS_KEY),
        help=t(
            "Leaves implausible or far-off prices out of every statistic. They stay in the "
            "downloads."
        ),
    )
    st.segmented_control(
        t("Outlier sensitivity"),
        [s.value for s in OutlierSensitivity],
        default=outlier_sensitivity(settings).value,
        key="_w_outlier_sensitivity",
        on_change=remember,
        args=("_w_outlier_sensitivity", SENSITIVITY_KEY),
        format_func=lambda value: t(str(value).capitalize()),
        disabled=not exclude,
        help="\n".join(f"- {t(s.label)}" for s in OutlierSensitivity),
    )


@st.cache_data(show_spinner=False)
def sample_status(database: str, db_version: float) -> tuple[int, int, int]:
    """(searches, failed, truncated) of the latest search run."""
    del db_version  # cache key only
    runs = SqliteRepository(database).all_runs()
    if not runs:
        return 0, 0, 0
    latest = max(runs, key=lambda run: run.started_at).run_id
    current = [run for run in runs if run.run_id == latest]
    failed = sum(1 for run in current if run.error)
    truncated = sum(1 for run in current if run.truncated)
    return len(current), failed, truncated


def load_market(settings: AppSettings, my_price: float | None) -> MarketData | None:
    version = database_version(settings)
    if version is None:
        return None
    with st.spinner(t("Loading your market data…")):
        return _load(
            str(config_path()),
            db_version=version,
            config_version=config_path().stat().st_mtime,
            my_price=my_price or 0.0,
            exclude_outliers=excluding_outliers(settings),
            sensitivity=outlier_sensitivity(settings).value,
        )


@st.cache_data(show_spinner=False)
def _load(
    config: str,
    *,
    db_version: float,
    config_version: float,
    my_price: float,
    exclude_outliers: bool,
    sensitivity: str,
) -> MarketData:
    del db_version, config_version  # cache keys only: reload when data or settings change
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
            outlier_policy=OutlierPolicy.for_sensitivity(OutlierSensitivity(sensitivity)),
            property_groups=settings.search.comp_set_groups,
        )
    )
    dataset = container.export_dataset.build(
        ExportQuery(
            as_of=now,
            my_listing_id=my_id,
            my_location=settings.search.center,
            min_nights_window_days=settings.enrich.availability_window_days,
            outlier_policy=OutlierPolicy.for_sensitivity(OutlierSensitivity(sensitivity)),
            property_groups=settings.search.comp_set_groups,
        )
    )
    raw = {
        "listings": pd.DataFrame(listing_rows(dataset.listings)),
        "pricing_snapshots": pd.DataFrame(pricing_rows(dataset.snapshots)),
        "calendar_snapshots": pd.DataFrame(calendar_rows(dataset.calendars)),
        "reviews": pd.DataFrame(review_rows(dataset.reviews)),
        "search_runs": pd.DataFrame(search_run_rows(dataset.search_runs)),
    }
    comp_set = _comp_set_tables(raw, settings.search.comp_set_groups)
    tables = dict(comp_set)
    flagged = {str(flag.listing_id) for flag in bundle.outliers}
    pricing = tables["pricing_snapshots"]
    if exclude_outliers and flagged and not pricing.empty:
        tables["pricing_snapshots"] = pricing[~pricing["listing_id"].isin(flagged)]
    return MarketData(bundle=bundle, tables=tables, raw_tables=raw, comp_set_tables=comp_set)


def _comp_set_tables(
    raw: dict[str, pd.DataFrame], groups: frozenset[PropertyGroup] | None
) -> dict[str, pd.DataFrame]:
    """Only listings of the comparable property groups (yours is always kept)."""
    tables = dict(raw)
    listings = raw["listings"]
    if not groups or listings.empty:
        return tables
    wanted = {g.value for g in groups}
    keep = listings[listings["property_group"].isin(wanted) | listings["is_mine"]]
    ids = set(keep["listing_id"])
    tables["listings"] = keep
    for name in ("pricing_snapshots", "calendar_snapshots", "reviews"):
        frame = raw[name]
        tables[name] = frame[frame["listing_id"].isin(ids)] if not frame.empty else frame
    return tables


def my_price_input(settings: AppSettings) -> int | None:
    """Your nightly price, shared by every page that shows a position."""
    configured = settings.my_listing.nightly_price
    return money_input(
        t("Your nightly price"),
        key=_MY_PRICE,
        currency=settings.scraping.currency,
        default=round(configured) if configured else None,
        help=t(
            "Type the price you charge (or plan to charge) per night to see where it "
            "stands. It is not saved; set a default in Set up my market."
        ),
    )


def money(amount: float | None, currency: str) -> str:
    return format_money(amount, currency)


def ago(moment: datetime | None) -> str:
    if moment is None:
        return t("never")
    minutes = int((datetime.now(UTC) - moment).total_seconds() // 60)
    if minutes < 1:
        return t("just now")
    if minutes < 60:
        return t("{minutes} min ago", minutes=minutes)
    if minutes < 48 * 60:
        return t("{hours} h ago", hours=minutes // 60)
    return t("{days} days ago", days=minutes // (24 * 60))
