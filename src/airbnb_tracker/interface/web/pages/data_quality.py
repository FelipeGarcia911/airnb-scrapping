"""Data quality: which prices were treated as outliers, why, and what was kept."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.domain.outliers import OutlierFlag, OutlierPolicy
from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import caveat, how_to_read, insight, section
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.theme import CORAL, MUTED

DEEP_DISCOUNT_PCT = 40


def render() -> None:
    st.title("Data quality")
    st.caption("Step 3 of 5 · Market analytics")
    st.markdown(
        "Some listings show prices nobody would pay (a host blocking dates with a huge "
        "price, a long-term rental) and a few sit far outside their segment. They are "
        "detected automatically and left out of the statistics, without deleting anything."
    )
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return
    currency = settings.scraping.currency
    raw_pricing, listings = data.comp_set_tables["pricing_snapshots"], data.listings
    flags = data.bundle.outliers

    deep = analytics.deep_discounts(raw_pricing, listings, DEEP_DISCOUNT_PCT)
    priced = analytics.latest_pricing(raw_pricing)["listing_id"].nunique()
    c1, c2, c3 = st.columns(3)
    c1.metric("Comp set listings priced", priced,
              help="Comparable property kinds only (see step 1), outliers included.")  # fmt: skip
    c2.metric("Price outliers", len(flags),
              help="Left out of the statistics while the sidebar switch is on.")  # fmt: skip
    c3.metric(f"Deep discounts (≥{DEEP_DISCOUNT_PCT}%)", deep["listing_id"].nunique(),
              help="Real, aggressive prices: kept in the statistics.")  # fmt: skip

    _outliers(flags, listings, currency, data.bundle.outliers_excluded)
    _distribution(raw_pricing, listings, data.outlier_ids, currency)
    _deep_discounts(deep, currency)
    _method()


def _outliers(
    flags: tuple[OutlierFlag, ...], listings: pd.DataFrame, currency: str, excluded: bool
) -> None:
    section(
        "Price outliers",
        "Each listing is judged on the median of its prices, against listings with the same "
        "number of bedrooms.",
    )
    if not flags:
        insight("No price outliers in the latest data: every price looks plausible.")
        return
    info = {str(record["listing_id"]): record for record in listings.to_dict("records")}
    rows = []
    for flag in sorted(flags, key=lambda f: -f.ratio):
        listing_id = str(flag.listing_id)
        row = info.get(listing_id)
        rows.append(
            {
                "Listing": row["title"] if row is not None else listing_id,
                "Why": flag.reason.label,
                "Typical nightly": round(flag.nightly),
                "Segment": flag.segment,
                "Segment median": round(flag.segment_median),
                "Times the median": round(flag.ratio, 1),
                "Reviews": analytics.as_float(row["review_count"]) if row is not None else None,
                "Airbnb": row["url"] if row is not None else None,
            }
        )
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Typical nightly": amount,
            "Segment median": amount,
            "Times the median": st.column_config.NumberColumn(format="%.1f×"),
            "Airbnb": st.column_config.LinkColumn(display_text="Open"),
        },
    )
    worst = max(flags, key=lambda f: f.ratio)
    state = "left out of" if excluded else "currently included in"
    insight(
        f"**{len(flags)}** listing{'s' if len(flags) != 1 else ''} {state} the statistics. "
        f"The most extreme asks **{format_money(worst.nightly, currency)}** per night, "
        f"**{worst.ratio:.0f}×** the median of its segment ({worst.segment})."
    )


def _distribution(
    pricing: pd.DataFrame, listings: pd.DataFrame, flagged: set[str], currency: str
) -> None:
    section(
        "How prices are distributed",
        "Typical nightly price of every listing by segment, on a logarithmic scale so that "
        "both ends are visible. Outliers in coral.",
    )
    latest = analytics.latest_pricing(pricing)
    if latest.empty:
        return
    per_listing = latest.groupby("listing_id")["nightly_price"].median().rename("nightly")
    frame = listings.set_index("listing_id")[["title", "bedrooms"]].join(per_listing, how="inner")
    frame = frame.reset_index()
    frame["Segment"] = frame["bedrooms"].map(
        lambda b: "unknown size" if pd.isna(b) else f"{int(b)} bedrooms"
    )
    frame["Status"] = frame["listing_id"].map(lambda i: "Outlier" if i in flagged else "Kept")
    chart = (
        alt.Chart(frame)
        .mark_tick(thickness=2, size=22)
        .encode(
            x=alt.X("nightly:Q", title=f"Typical nightly price ({currency}, log scale)",
                    scale=alt.Scale(type="log")),
            y=alt.Y("Segment:N", title=None),
            color=alt.Color("Status:N", scale=alt.Scale(domain=["Kept", "Outlier"],
                            range=[MUTED, CORAL])),
            tooltip=["title", "Segment", alt.Tooltip("nightly:Q", format=",.0f"), "Status"],
        )
        .properties(height=60 + 40 * frame["Segment"].nunique())
    )  # fmt: skip
    st.altair_chart(chart, width="stretch")
    how_to_read(
        "- Each tick is one listing. Most listings cluster together; the further a tick "
        "sits from its cluster, the more unusual its price.\n"
        "- On a log scale, equal distances mean equal ratios: twice as expensive is always "
        "the same distance, whatever the price."
    )


def _deep_discounts(deep: pd.DataFrame, currency: str) -> None:
    section(
        "Deep discounts (kept)",
        f"Prices at least {DEEP_DISCOUNT_PCT}% below the listing's own original price. They "
        "are real offers, so they stay in the statistics; they show aggressive competitors.",
    )
    if deep.empty:
        st.caption("No deep discounts in the latest data.")
        return
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        deep.drop(columns="listing_id"),
        hide_index=True,
        width="stretch",
        column_config={
            "Original nightly": amount,
            "Nightly paid": amount,
            "Discount": st.column_config.NumberColumn(format="%.0f%%"),
        },
    )
    top = deep.iloc[0]
    insight(
        f"**{top['Listing']}** cuts **{top['Discount']:.0f}%** for {top['Nights']}-night stays "
        f"(to {format_money(top['Nightly paid'], currency)} per night). Competitors this "
        "aggressive set the floor for long stays."
    )


def _method() -> None:
    policy = OutlierPolicy()
    with st.expander("How outliers are detected", icon=":material/science:"):
        st.markdown(
            "A naive cut-off fails both ways: it would drop genuine luxury listings and "
            "aggressive discounts. So each listing is judged on the **median of all its "
            "prices**, within its **segment** (same number of bedrooms; the whole market if "
            f"the segment has fewer than {policy.min_segment_size} listings), and flagged "
            "only when two tests agree:\n\n"
            f"1. **Statistically extreme**: robust z-score above {policy.z_threshold} "
            "(computed with the median and MAD of log prices, so a few extreme values cannot "
            "hide themselves).\n"
            f"2. **Far in relative terms**: at least {policy.high_ratio}× the segment median, "
            f"or at most {policy.low_ratio}× of it.\n\n"
            f"Prices of {policy.placeholder_ratio:g}× the segment median or more are always "
            "flagged: as an **implausible price** when the listing has never been booked "
            "(no reviews), otherwise as **far above its segment** (luxury or a different "
            "kind of property, such as a country estate among city flats)."
        )
    caveat("Outliers are excluded from statistics only: they stay in every download, "
           "with a price_outlier column in listings.")  # fmt: skip
