"""Results tab: market prices per stay length, your position, a map and the raw tables."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import DurationReport, ReportBundle
from airbnb_tracker.application.use_cases import BuildReportQuery, ExportQuery
from airbnb_tracker.bootstrap import Container
from airbnb_tracker.domain.value_objects import Money
from airbnb_tracker.infrastructure.config import AppSettings, ConfigurationError, load_settings
from airbnb_tracker.infrastructure.exporters.dataset import (
    calendar_rows,
    listing_rows,
    pricing_rows,
    review_rows,
    search_run_rows,
)
from airbnb_tracker.interface.web.theme import CORAL, MUTED

_POSITION = {"below_market": "Below market", "competitive": "Competitive", "premium": "Premium"}


def render(config_path: Path) -> None:
    try:
        settings = load_settings(config_path)
    except ConfigurationError as error:
        st.info(f"Save a valid configuration in **Search setup** first. ({error})")
        return
    if not settings.storage.database_path.exists():
        st.info("No data yet. Start a run in the **Run** tab.")
        return

    default_price = float(settings.my_listing.nightly_price or 0)
    price = st.number_input(
        f"Your nightly price ({settings.scraping.currency})",
        min_value=0.0,
        value=default_price,
        step=5000.0,
        help="Try different prices to see where you would stand.",
    )
    db_version = settings.storage.database_path.stat().st_mtime
    bundle, tables = _load(str(config_path), db_version, price)

    _headline(bundle, tables)
    _price_section(bundle)
    _map_section(tables, settings)
    _tables_section(tables)


@st.cache_data(show_spinner="Loading data…")
def _load(
    config_path: str, db_version: float, price: float
) -> tuple[ReportBundle, dict[str, pd.DataFrame]]:
    """Cached per database version: reruns are instant until new data arrives."""
    del db_version  # only part of the cache key
    settings = load_settings(Path(config_path))
    container = Container(settings)
    now = datetime.now(UTC)
    my_id = settings.my_listing.listing_id
    bundle = container.build_report.execute(
        BuildReportQuery(
            stays=settings.stays.resolve(now.date()),
            my_listing_id=my_id,
            my_nightly_price=Money(Decimal(str(price)), settings.scraping.currency)
            if price
            else None,
            availability_window_days=settings.enrich.availability_window_days,
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
    tables = {
        "listings": pd.DataFrame(listing_rows(dataset.listings)),
        "pricing_snapshots": pd.DataFrame(pricing_rows(dataset.snapshots)),
        "calendar_snapshots": pd.DataFrame(calendar_rows(dataset.calendars)),
        "reviews": pd.DataFrame(review_rows(dataset.reviews)),
        "search_runs": pd.DataFrame(search_run_rows(dataset.search_runs)),
    }
    return bundle, tables


def _headline(bundle: ReportBundle, tables: dict[str, pd.DataFrame]) -> None:
    listings = tables["listings"]
    enriched = listings[listings["details_collected_at"].notna()] if len(listings) else listings
    availability = bundle.availability
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Listings priced", len(listings))
    c2.metric("Fully analysed", len(enriched))
    c3.metric(
        "Unavailable days (median)",
        f"{availability.median_unavailable_pct:.0f}%"
        if availability.median_unavailable_pct is not None
        else "—",
        help="Next days not bookable: bookings, host blocks or stay restrictions.",
    )
    c4.metric(
        "Minimum stay (median)",
        f"{availability.median_min_nights:g} nights"
        if availability.median_min_nights is not None
        else "—",
    )
    if bundle.data_captured_at:
        st.caption(f"Prices collected {bundle.data_captured_at:%Y-%m-%d %H:%M} UTC")


def _price_section(bundle: ReportBundle) -> None:
    st.subheader("Nightly price by stay length")
    if not bundle.durations:
        st.info("No prices for the configured scenarios yet: run a price scan.")
        return
    frame = pd.DataFrame([_duration_row(d) for d in bundle.durations])
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "P25": st.column_config.NumberColumn(format="localized"),
            "Median": st.column_config.NumberColumn(format="localized"),
            "P75": st.column_config.NumberColumn(format="localized"),
            "Monthly median": st.column_config.NumberColumn(
                format="localized", help="Median nightly price x 30"
            ),
            "Median discount": st.column_config.NumberColumn(format="%.0f%%"),
            "Discounted": st.column_config.ProgressColumn(
                format="%.0f%%", min_value=0, max_value=100
            ),
        },
    )
    st.bar_chart(frame, x="Nights", y=["P25", "Median", "P75"], stack=False, height=260)


def _duration_row(report: DurationReport) -> dict[str, object]:
    dist = report.distribution

    def amount(money: Money | None) -> float | None:
        return float(money.amount) if money else None

    median = amount(dist.median) if dist else None
    return {
        "Nights": report.nights,
        "Check-ins": report.check_ins,
        "Listings": report.listings,
        "P25": amount(dist.p25) if dist else None,
        "Median": median,
        "P75": amount(dist.p75) if dist else None,
        "Monthly median": median * 30 if median else None,
        "Median discount": float(report.median_discount_pct)
        if report.median_discount_pct
        else None,
        "Discounted": report.discounted_share_pct,
        "Your position": (
            f"{_POSITION[report.position.value]} · above {report.percentile:.0f}%"
            if report.position and report.percentile is not None
            else "—"
        ),
    }


def _map_section(tables: dict[str, pd.DataFrame], settings: AppSettings) -> None:
    listings = tables["listings"]
    if listings.empty:
        return
    st.subheader("Where they are")
    points = listings.dropna(subset=["latitude", "longitude"]).copy()
    points["color"] = points["is_mine"].map({True: CORAL, False: MUTED})
    points["size"] = points["is_mine"].map({True: 80, False: 35})
    st.map(points, latitude="latitude", longitude="longitude", color="color", size="size",
           height=380)  # fmt: skip
    st.caption(f"Your listing in coral · search radius {settings.search.radius_km:g} km")


def _tables_section(tables: dict[str, pd.DataFrame]) -> None:
    st.subheader("Data")
    tabs = st.tabs(["Listings", "Prices", "Calendars", "Reviews", "Searches"])
    names = list(tables)
    for tab, name in zip(tabs, names, strict=True):
        with tab:
            frame = tables[name]
            if name == "listings":
                frame = _listings_view(frame, tables["pricing_snapshots"])
            st.dataframe(
                frame,
                hide_index=True,
                width="stretch",
                height=420,
                column_config={"url": st.column_config.LinkColumn("url", display_text="Open")},
            )
            st.download_button(
                f"Download {name}.csv",
                frame.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{name}.csv",
                mime="text/csv",
                icon=":material/download:",
                key=f"download_{name}",
            )


def _listings_view(listings: pd.DataFrame, pricing: pd.DataFrame) -> pd.DataFrame:
    """Listings with their median nightly price per stay length, filterable."""
    if listings.empty:
        return listings
    only_analysed = st.toggle("Only fully analysed listings", value=True)
    view = listings[listings["details_collected_at"].notna()] if only_analysed else listings
    if not pricing.empty:
        medians = (
            pricing.groupby(["listing_id", "nights"])["nightly_price"].median().unstack("nights")
        )
        medians.columns = [f"nightly_{n}n" for n in medians.columns]
        view = view.merge(medians, left_on="listing_id", right_index=True, how="left")
    first = ["is_mine", "title", "distance_km", "bedrooms", "bathrooms", "max_guests",
             "minimum_nights", "rating", "review_count", "reviews_last_90_days"]  # fmt: skip
    price_columns = [c for c in view.columns if c.startswith("nightly_")]
    rest = [c for c in view.columns if c not in first and c not in price_columns]
    return view[[c for c in first if c in view.columns] + price_columns + rest]
