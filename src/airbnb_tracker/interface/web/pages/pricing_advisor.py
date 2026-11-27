"""Pricing advisor: a suggested (re)opening rate from your closest comparables."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.domain.earnings import base_for_effective
from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import (
    caveat,
    how_to_read,
    insight,
    nights_picker,
    section,
)
from airbnb_tracker.interface.web.formatting import format_money
from airbnb_tracker.interface.web.glossary import BASE_RATE, NIGHTLY_AFTER_DISCOUNT, glossary
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.navigation import go_to
from airbnb_tracker.interface.web.pages.earnings import (
    CALCULATOR_NIGHTS,
    PENDING_NIGHTLY_AFTER_DISCOUNT,
)
from airbnb_tracker.interface.web.theme import CORAL, MUTED, TEAL

_GO_TO_CALCULATOR = "_go_to_calculator"
_EFFECT = {1: "▲ raises", 0: "— neutral", -1: "▼ lowers"}


def render() -> None:
    st.title(t("Pricing advisor"))
    st.caption(t("Pricing"))
    st.markdown(
        t(
            "A suggested nightly rate for **your** listing, based on the competitors most like "
            "it, then nudged by what makes yours stronger or weaker. Use it as a starting point "
            "for reopening or repricing, not as an automatic price."
        )
    )
    glossary()
    settings = require_settings()
    if settings is None:
        return
    data = require_data(settings)
    if data is None:
        return
    currency = settings.scraping.currency
    listings, pricing = data.listings, data.tables["pricing_snapshots"]
    mine = analytics.my_listing(listings)
    if mine is None:
        st.warning(t("Add your listing in step 1 so the advisor knows what to compare."))
        return

    with st.container(border=True):
        st.markdown(
            t(
                "**Your listing:** {title} · {layout} · rating {rating} ({reviews} reviews)",
                title=mine["title"],
                layout=_layout(mine),
                rating=mine["rating"] if pd.notna(mine["rating"]) else "—",
                reviews=int(mine["review_count"]),
            )
        )
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            nights = nights_picker(pricing["nights"].unique(), key="advisor_nights")
        max_km = c2.slider(t("Max distance (km)"), 1, int(max(settings.search.radius_km, 1)),
                           min(3, int(settings.search.radius_km)), key="advisor_km")  # fmt: skip
        same_bedrooms = c3.toggle(
            t("Same bedrooms ({count})", count=_int(mine["bedrooms"])),
            value=True,
            key="advisor_bedrooms",
        )
        same_baths = c3.toggle(
            t("At least {count} bathrooms", count=_int(mine["bathrooms"])),
            value=True,
            key="advisor_baths",
        )

    nightly = analytics.nightly_by_listing(pricing, nights)
    comps = analytics.comparables(
        listings,
        nightly,
        max_km=max_km,
        same_bedrooms=same_bedrooms,
        min_bathrooms=float(mine["bathrooms"])
        if same_baths and pd.notna(mine["bathrooms"])
        else None,
    )
    advice = analytics.advise_price(listings, comps, nights)
    if advice is None:
        st.warning(
            t(
                "Only {count} comparables match. Widen the distance or relax the filters "
                "(at least {needed} are needed).",
                count=len(comps),
                needed=analytics.MIN_COMPARABLES,
            )
        )
        return

    _headline(advice, currency, settings.my_listing.discount_for(nights))
    _context(advice, nightly, currency)
    _percentile_explorer(advice, currency)
    _signals(advice)
    _seasonal(pricing, advice, currency)
    _comparables_table(advice)


def _context(advice: analytics.PriceAdvice, nightly: pd.Series, currency: str) -> None:
    """Why the suggestion may differ from the market median, and how much to trust it."""
    market = float(nightly.median()) if len(nightly) else None
    if market:
        gap = (advice.median / market - 1) * 100
        if abs(gap) >= 15:
            text = (
                "Your closest comparables charge **{gap:.0f}% more** than the comp set "
                "overall (median {median} vs {market}): listings like yours (same bedrooms, "
                "bathrooms and area) are a different price tier. Relax the filters above to "
                "compare with the wider market."
                if gap > 0
                else "Your closest comparables charge **{gap:.0f}% less** than the comp set "
                "overall (median {median} vs {market}): listings like yours (same bedrooms, "
                "bathrooms and area) are a different price tier. Relax the filters above to "
                "compare with the wider market."
            )
            message = t(
                text,
                gap=abs(gap),
                median=format_money(advice.median, currency),
                market=format_money(market, currency),
            )
            st.caption(f":material/info: {message}")
    if len(advice.comparables) < analytics.CONFIDENT_COMPARABLES:
        message = t(
            "Only {count} comparables: a single listing moves the percentiles a lot. Widen "
            "the distance for a steadier estimate.",
            count=len(advice.comparables),
        )
        st.caption(f":orange[:material/warning: {message}]")


def _headline(advice: analytics.PriceAdvice, currency: str, my_discount: float) -> None:
    section(
        t("Suggested rate"),
        t(
            "From {count} comparables, {nights}-night stays.",
            count=len(advice.comparables),
            nights=advice.nights,
        ),
    )
    base = base_for_effective(advice.suggested, my_discount)
    c1, c2, c3 = st.columns(3)
    c1.metric(
        t("Suggested nightly after discount"),
        format_money(advice.suggested, currency),
        help=t("Percentile {pct} of comparables", pct=advice.target_percentile),
    )
    c2.metric(
        t(BASE_RATE),
        format_money(base, currency),
        help=t(
            "What to publish so that your {discount:g}% discount for {nights}-night stays "
            "brings it to the suggested price (set your discounts in Set up my market).",
            discount=my_discount,
            nights=advice.nights,
        ),
    )
    c3.metric(
        t("Competitive range"),
        f"{format_money(advice.p25, currency)} – {format_money(advice.p75, currency)}",
        help=t("P25–P75 of comparables: half of them charge within this range."),
    )
    position = (
        t("upper half") if advice.target_percentile > 50
        else t("lower half") if advice.target_percentile < 50 else t("middle")
    )  # fmt: skip
    monthly = (
        t("(≈ **{monthly}** per month) ", monthly=format_money(advice.monthly, currency))
        if advice.monthly
        else ""
    )
    insight(
        t(
            "Aim for **{suggested}** nightly after discount {monthly}to sit in the "
            "**{position}** of your comparables (percentile {pct}). Staying between {p25} "
            "and {p75} keeps you competitive. With your {discount:g}% discount, publish a "
            "base nightly rate of **{base}**.",
            suggested=format_money(advice.suggested, currency),
            monthly=monthly,
            position=position,
            pct=advice.target_percentile,
            p25=format_money(advice.p25, currency),
            p75=format_money(advice.p75, currency),
            discount=my_discount,
            base=format_money(base, currency),
        )
    )


def _percentile_explorer(advice: analytics.PriceAdvice, currency: str) -> None:
    section(
        t("Explore other positions"),
        t(
            "Pick a percentile to see the nightly price that puts you there: P20 means 20% of "
            "comparables are cheaper than you, P80 means 80% are."
        ),
    )
    prices = advice.comparables["nightly"]
    chosen = st.slider(
        t("Percentile"),
        min_value=5,
        max_value=95,
        value=advice.target_percentile,
        step=1,
        format="P%d",
        key="advisor_percentile",
    )
    price = float(prices.quantile(chosen / 100))
    cheaper = int((prices < price).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric(
        t("{label} at P{pct}", label=t(NIGHTLY_AFTER_DISCOUNT), pct=chosen),
        format_money(price, currency),
        delta=t("{gap:+.0f}% vs suggested", gap=(price / advice.suggested - 1) * 100),
        delta_color="off",
    )
    if advice.nights >= analytics.MONTHLY_STAY_NIGHTS:
        c2.metric(t("Monthly equivalent"), format_money(price * 30, currency))
    c3.metric(
        t("Comparables cheaper than this"),
        t("{count} of {total}", count=cheaper, total=len(prices)),
    )

    st.altair_chart(_position_chart(advice, chosen, price, currency), width="stretch")

    nightly_column, monthly_column = t("Nightly price"), t("Monthly")
    ladder = pd.DataFrame(
        {
            t("Percentile"): [f"P{p}" for p in range(10, 100, 10)],
            nightly_column: [round(float(prices.quantile(p / 100))) for p in range(10, 100, 10)],
        }
    )
    if advice.nights >= analytics.MONTHLY_STAY_NIGHTS:
        ladder[monthly_column] = ladder[nightly_column] * 30
    with st.expander(t("Price at every 10th percentile")):
        amount = st.column_config.NumberColumn(format="localized")
        st.dataframe(ladder, hide_index=True, width="stretch",
                     column_config={nightly_column: amount, monthly_column: amount})  # fmt: skip

    st.button(
        t("Calculate earnings at {price}", price=format_money(price, currency)),
        icon=":material/calculate:",
        on_click=_send_to_calculator,
        args=(round(price), advice.nights),
        help=t("Opens the earnings calculator with this price and stay length."),
    )
    if st.session_state.pop(_GO_TO_CALCULATOR, False):
        go_to("earnings")


def _position_chart(
    advice: analytics.PriceAdvice, chosen: int, price: float, currency: str
) -> alt.LayerChart | alt.FacetChart:
    comps = advice.comparables[["title", "nightly"]]
    band = (
        alt.Chart(pd.DataFrame({"low": [advice.p25], "high": [advice.p75]}))
        .mark_rect(color=CORAL, opacity=0.08)
        .encode(x="low:Q", x2="high:Q")
    )
    ticks = (
        alt.Chart(comps)
        .mark_tick(thickness=2, size=34, color=MUTED)
        .encode(
            x=alt.X(
                "nightly:Q",
                title=t("Nightly price ({currency})", currency=currency),
                scale=alt.Scale(zero=False),
            ),
            tooltip=[
                alt.Tooltip("title:N", title=t("Listing")),
                alt.Tooltip("nightly:Q", title=t("Nightly price"), format=",.0f"),
            ],
        )
    )
    markers = pd.DataFrame(
        {
            "price": [advice.suggested, price],
            "label": [
                t("▼ Suggested · P{pct}", pct=advice.target_percentile),
                t("▲ Your choice · P{pct}", pct=chosen),
            ],
            "who": ["suggested", "chosen"],
        }
    )
    colors = alt.Scale(domain=["suggested", "chosen"], range=[CORAL, TEAL])
    rules = (
        alt.Chart(markers)
        .mark_rule(strokeWidth=3)
        .encode(x="price:Q", color=alt.Color("who:N", scale=colors, legend=None))
    )
    labels = [
        alt.Chart(markers[markers["who"] == who])
        .mark_text(dy=dy, fontWeight="bold", fontSize=13)
        .encode(x="price:Q", text="label:N", color=alt.Color("who:N", scale=colors, legend=None))
        for who, dy in (("suggested", -32), ("chosen", 32))
    ]
    return alt.layer(band, ticks, rules, *labels).properties(height=150)


def _send_to_calculator(price: int, nights: int) -> None:
    # Comparables are priced after their discounts: the calculator converts this into
    # the base rate to publish, using your own discount for that stay length.
    st.session_state[PENDING_NIGHTLY_AFTER_DISCOUNT] = price
    st.session_state[CALCULATOR_NIGHTS] = nights
    st.session_state[_GO_TO_CALCULATOR] = True


def _signals(advice: analytics.PriceAdvice) -> None:
    section(t("Why this percentile"),
            t("Starts at the median (P50); each strength moves it up 8 percentiles, each "
              "weakness down 8, within P25–P75."))  # fmt: skip
    if not advice.signals:
        st.caption(t("Not enough information about your listing to adjust the median."))
        return
    table = pd.DataFrame(
        [
            {t("Factor"): t(s.factor), t("Yours"): s.yours,
             t("Comparables (typical)"): s.comparables, t("Effect"): t(_EFFECT[s.effect])}
            for s in advice.signals
        ]
    )  # fmt: skip
    st.dataframe(table, hide_index=True, width="stretch")
    caveat(t("Rules are simple and transparent on purpose: adjust them with your own judgement."))


def _seasonal(pricing: pd.DataFrame, advice: analytics.PriceAdvice, currency: str) -> None:
    table = analytics.seasonal_prices(pricing, advice.comparables, advice.nights,
                                      advice.suggested)  # fmt: skip
    if table.empty:
        return
    factors = analytics.by_label(table["Season factor"])
    section(
        t("Suggested rate by check-in date"),
        t(
            "The same rate adjusted to how comparables price each check-in date (season factor "
            "= that date's median ÷ the overall median)."
        ),
    )
    view = table.rename(columns=t).rename_axis(t(str(table.index.name)))
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        view,
        width="stretch",
        column_config={
            t("Comparables priced"): st.column_config.NumberColumn(
                help=t("Comparables still available, and so priced, for that check-in.")
            ),
            t("Comparables median"): amount,
            t("Suggested nightly after discount"): amount,
            t("Suggested monthly"): amount,
        },
    )
    high, low = max(factors, key=factors.__getitem__), min(factors, key=factors.__getitem__)
    insight(
        t(
            "Check-ins around **{high}** support the highest rate ({high_factor:.2f}× the "
            "median); around **{low}** the lowest ({low_factor:.2f}×).",
            high=high,
            high_factor=factors[high],
            low=low,
            low_factor=factors[low],
        )
    )
    caveat(
        t(
            "In busy periods the best listings are already booked, so the ones still "
            "available (and priced) are often cheaper. Few comparables priced on a date means "
            "a weaker signal."
        )
    )


def _comparables_table(advice: analytics.PriceAdvice) -> None:
    with st.expander(t("The {count} comparables used", count=len(advice.comparables))):
        names = {
            "title": t("Listing"),
            "distance_km": t("Km"),
            "bedrooms": t("Bedrooms"),
            "bathrooms": t("Baths"),
            "rating": t("Rating"),
            "review_count": t("Reviews"),
            "nightly": t("Nightly"),
            "url": "Airbnb",
        }
        view = advice.comparables[list(names)].rename(columns=names)
        st.dataframe(
            view.sort_values(names["nightly"]),
            hide_index=True,
            width="stretch",
            column_config={
                names["nightly"]: st.column_config.NumberColumn(format="localized"),
                names["distance_km"]: st.column_config.NumberColumn(format="%.1f"),
                "Airbnb": st.column_config.LinkColumn(display_text=t("Open")),
            },
        )
    how_to_read(
        t(
            "- **Comparables**: priced listings within the distance, with your bedrooms and "
            "at least your bathrooms (toggle to relax).\n"
            "- **Nightly** is each comparable's median across the check-in dates searched.\n"
            "- **Prices exclude cleaning fees and taxes**, like everywhere in this app."
        )
    )


def _layout(mine: pd.Series) -> str:
    return t(
        "{bedrooms} bedrooms · {baths} baths",
        bedrooms=_int(mine["bedrooms"]),
        baths=_int(mine["bathrooms"]),
    )


def _int(value: object) -> str:
    number = analytics.as_float(value)
    return "—" if number is None else f"{number:g}"
