"""Data quality: which prices were treated as outliers, why, and what was kept."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.domain.outliers import OutlierFlag, OutlierPolicy
from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import caveat, how_to_read, insight, section
from airbnb_tracker.interface.web.data import MarketData, analysis_controls, load_market
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.theme import CORAL, MUTED

DEEP_DISCOUNT_PCT = 40


def render() -> None:
    st.title(t("Data quality"))
    st.caption(t("Setup & data"))
    st.markdown(
        t(
            "Some listings show prices nobody would pay (a host blocking dates with a huge "
            "price, a long-term rental) and a few sit far outside their segment. They are "
            "detected automatically and left out of the statistics, without deleting anything."
        )
    )
    settings = require_settings()
    if settings is None:
        return
    with st.container(border=True):
        st.markdown(f"**{t('Analysis settings')}** · {t('apply to every page')}")
        analysis_controls(settings)
    _search_audit(load_market(settings, None))
    data = require_data(settings)
    if data is None:
        return
    currency = settings.scraping.currency
    raw_pricing, listings = data.comp_set_tables["pricing_snapshots"], data.listings
    flags = data.bundle.outliers

    deep = analytics.deep_discounts(raw_pricing, listings, DEEP_DISCOUNT_PCT)
    priced = analytics.latest_pricing(raw_pricing)["listing_id"].nunique()
    c1, c2, c3 = st.columns(3)
    c1.metric(t("Comp set listings priced"), priced,
              help=t("Comparable property kinds only (see step 1), outliers included."),
              )  # fmt: skip
    c2.metric(t("Price outliers"), len(flags),
              help=t("Left out of the statistics while the analysis switch is on."))  # fmt: skip
    c3.metric(t("Deep discounts (≥{pct}%)", pct=DEEP_DISCOUNT_PCT), deep["listing_id"].nunique(),
              help=t("Real, aggressive prices: kept in the statistics."))  # fmt: skip

    _outliers(flags, listings, currency, data.bundle.outliers_excluded)
    _distribution(raw_pricing, listings, data.outlier_ids, currency)
    _deep_discounts(deep, currency)
    _method()


def _outliers(
    flags: tuple[OutlierFlag, ...], listings: pd.DataFrame, currency: str, excluded: bool
) -> None:
    section(
        t("Price outliers"),
        t(
            "Each listing is judged on the median of its prices, against listings with the "
            "same number of bedrooms."
        ),
    )
    if not flags:
        insight(t("No price outliers in the latest data: every price looks plausible."))
        return
    info = {str(record["listing_id"]): record for record in listings.to_dict("records")}
    rows = []
    for flag in sorted(flags, key=lambda f: -f.ratio):
        listing_id = str(flag.listing_id)
        row = info.get(listing_id)
        rows.append(
            {
                t("Listing"): row["title"] if row is not None else listing_id,
                t("Why"): t(flag.reason.label),
                t("Typical nightly"): round(flag.nightly),
                t("Segment"): _segment(flag.segment),
                t("Segment median"): round(flag.segment_median),
                t("Times the median"): round(flag.ratio, 1),
                t("Reviews"): analytics.as_float(row["review_count"]) if row is not None else None,
                "Airbnb": row["url"] if row is not None else None,
            }
        )
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            t("Typical nightly"): amount,
            t("Segment median"): amount,
            t("Times the median"): st.column_config.NumberColumn(format="%.1f×"),
            "Airbnb": st.column_config.LinkColumn(display_text=t("Open")),
        },
    )
    worst = max(flags, key=lambda f: f.ratio)
    if excluded:
        count = (
            t("**1** listing left out of the statistics.")
            if len(flags) == 1
            else t("**{count}** listings left out of the statistics.", count=len(flags))
        )
    else:
        count = (
            t("**1** listing currently included in the statistics.")
            if len(flags) == 1
            else t("**{count}** listings currently included in the statistics.", count=len(flags))
        )
    extreme = t(
        "The most extreme asks **{price}** per night, **{ratio}×** the median of its "
        "segment ({segment}).",
        price=format_money(worst.nightly, currency),
        ratio=f"{worst.ratio:.0f}",
        segment=_segment(worst.segment),
    )
    insight(f"{count} {extreme}")  # fmt: skip


def _distribution(
    pricing: pd.DataFrame, listings: pd.DataFrame, flagged: set[str], currency: str
) -> None:
    section(
        t("How prices are distributed"),
        t(
            "Typical nightly price of every listing by segment, on a logarithmic scale so "
            "that both ends are visible. Outliers in coral."
        ),
    )
    latest = analytics.latest_pricing(pricing)
    if latest.empty:
        return
    per_listing = latest.groupby("listing_id")["nightly_price"].median().rename("nightly")
    frame = listings.set_index("listing_id")[["title", "bedrooms"]].join(per_listing, how="inner")
    frame = frame.reset_index()
    frame["Segment"] = frame["bedrooms"].map(
        lambda b: t("unknown size") if pd.isna(b) else t("{count} bedrooms", count=int(b))
    )
    outlier, kept = t("Outlier"), t("Kept")
    frame["Status"] = frame["listing_id"].map(lambda i: outlier if i in flagged else kept)
    chart = (
        alt.Chart(frame)
        .mark_tick(thickness=2, size=22)
        .encode(
            x=alt.X("nightly:Q", scale=alt.Scale(type="log"),
                    title=t("Typical nightly price ({currency}, log scale)", currency=currency)),
            y=alt.Y("Segment:N", title=None),
            color=alt.Color("Status:N", title=t("Status"),
                            scale=alt.Scale(domain=[kept, outlier], range=[MUTED, CORAL])),
            tooltip=[alt.Tooltip("title:N", title=t("Listing")),
                     alt.Tooltip("Segment:N", title=t("Segment")),
                     alt.Tooltip("nightly:Q", title=t("Typical nightly"), format=",.0f"),
                     alt.Tooltip("Status:N", title=t("Status"))],
        )
        .properties(height=60 + 40 * frame["Segment"].nunique())
    )  # fmt: skip
    st.altair_chart(chart, width="stretch")
    how_to_read(
        t(
            "- Each tick is one listing. Most listings cluster together; the further a tick "
            "sits from its cluster, the more unusual its price.\n"
            "- On a log scale, equal distances mean equal ratios: twice as expensive is always "
            "the same distance, whatever the price."
        )
    )


def _deep_discounts(deep: pd.DataFrame, currency: str) -> None:
    section(
        t("Deep discounts (kept)"),
        t(
            "Prices at least {pct}% below the listing's own original price. They are real "
            "offers, so they stay in the statistics; they show aggressive competitors.",
            pct=DEEP_DISCOUNT_PCT,
        ),
    )
    if deep.empty:
        st.caption(t("No deep discounts in the latest data."))
        return
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        deep.drop(columns="listing_id").rename(columns=lambda name: t(str(name))),
        hide_index=True,
        width="stretch",
        column_config={
            t("Original nightly"): amount,
            t("Nightly paid"): amount,
            t("Discount"): st.column_config.NumberColumn(format="%.0f%%"),
        },
    )
    top = deep.iloc[0]
    insight(
        t(
            "**{listing}** cuts **{pct}%** for {nights}-night stays (to {price} per night). "
            "Competitors this aggressive set the floor for long stays.",
            listing=top["Listing"],
            pct=f"{top['Discount']:.0f}",
            nights=top["Nights"],
            price=format_money(top["Nightly paid"], currency),
        )
    )


_AUDIT_COLUMNS = {
    "check_in": "Check-in",
    "nights": "Nights",
    "status": "Status",
    "failure": "Failure",
    "rows_received": "Received",
    "rows_parsed": "Interpreted",
    "rows_duplicates": "Duplicates",
    "rows_filtered_out": "Outside area or filters",
    "rows_over_limit": "Beyond the limit",
    "rows_kept": "Kept",
    "rows_priced": "Valid price",
    "rows_price_excluded": "Price excluded",
}
_EXCLUSIONS = {
    "unknown_unit": "Unit missing or unknown",
    "nights_mismatch": "Nights shown differ from the search",
    "not_recorded": "Captured before units were recorded",
}


def _search_audit(data: MarketData | None) -> None:
    """Latest run under the current conditions, search by search, and excluded prices."""
    if data is None:
        return
    runs = data.raw_tables["search_runs"]
    current = runs[runs["conditions_key"] == data.conditions_key] if not runs.empty else runs
    with st.expander(t("Searches and price interpretation"), icon=":material/fact_check:"):
        if current.empty:
            st.info(t("No search with the current conditions yet: collect data first."))
        else:
            latest = current[
                current["run_id"] == current.sort_values("started_at")["run_id"].iloc[-1]
            ]
            st.caption(t("Latest run with the current conditions · collector {version}",
                         version=latest["collector_version"].iloc[-1]))  # fmt: skip
            view = latest[list(_AUDIT_COLUMNS)].rename(columns=lambda c: t(_AUDIT_COLUMNS[c]))
            st.dataframe(view, hide_index=True, width="stretch")
        pricing = data.raw_tables["pricing_snapshots"]
        counts = pricing["excluded_reason"].value_counts().to_dict() if not pricing.empty else {}
        if counts:
            st.markdown(f"**{t('Prices left out of every statistic (whole history)')}**")
            for reason, count in counts.items():
                st.markdown(f"- {t(_EXCLUSIONS.get(str(reason), str(reason)))}: {count}")
        caveat(t("Excluded prices stay in the database and in the exports, with their reason."))


def _method() -> None:
    policy = OutlierPolicy()
    with st.expander(t("How outliers are detected"), icon=":material/science:"):
        st.markdown(
            t(
                "A naive cut-off fails both ways: it would drop genuine luxury listings and "
                "aggressive discounts. So each listing is judged on the **median of all its "
                "prices**, within its **segment** (same number of bedrooms; the whole market "
                "if the segment has fewer than {min_size} listings), and flagged only when two "
                "tests agree:\n\n"
                "1. **Statistically extreme**: robust z-score above {z} (computed with the "
                "median and MAD of log prices, so a few extreme values cannot hide "
                "themselves).\n"
                "2. **Far in relative terms**: at least {high}× the segment median, or at most "
                "{low}× of it.\n\n"
                "Prices of {placeholder}× the segment median or more are always flagged: as "
                "an **implausible price** when the listing has never been booked (no "
                "reviews), otherwise as **far above its segment** (luxury or a different kind "
                "of property, such as a country estate among city flats).",
                min_size=policy.min_segment_size,
                z=policy.z_threshold,
                high=policy.high_ratio,
                low=policy.low_ratio,
                placeholder=f"{policy.placeholder_ratio:g}",
            )
        )
    caveat(t("Outliers are excluded from statistics only: they stay in every download, "
             "with a price_outlier column in listings."))  # fmt: skip


def _segment(segment: str) -> str:
    """Translate a domain segment name such as "2 bedrooms" or "unknown size"."""
    count, _, rest = segment.partition(" ")
    if rest == "bedrooms" and count.isdigit():
        return t("{count} bedrooms", count=int(count))
    return t(segment)
