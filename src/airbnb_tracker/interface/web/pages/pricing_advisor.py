"""Pricing advisor: a suggested (re)opening rate from your closest comparables."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from airbnb_tracker.interface.web import analytics
from airbnb_tracker.interface.web.components import (
    caveat,
    how_to_read,
    insight,
    nights_picker,
    section,
)
from airbnb_tracker.interface.web.formatting import forget_money_input, format_money
from airbnb_tracker.interface.web.guards import require_data, require_settings
from airbnb_tracker.interface.web.navigation import go_to
from airbnb_tracker.interface.web.pages.earnings import CALCULATOR_NIGHTS, CALCULATOR_PRICE
from airbnb_tracker.interface.web.theme import CORAL, INK, MUTED

_GO_TO_CALCULATOR = "_go_to_calculator"
_EFFECT = {1: "▲ raises", 0: "— neutral", -1: "▼ lowers"}


def render() -> None:
    st.title("Pricing advisor")
    st.caption("Step 4 of 5 · Pricing & finances")
    st.markdown(
        "A suggested nightly rate for **your** listing, based on the competitors most like "
        "it, then nudged by what makes yours stronger or weaker. Use it as a starting point "
        "for reopening or repricing, not as an automatic price."
    )
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
        st.warning("Add your listing in step 1 so the advisor knows what to compare.")
        return

    with st.container(border=True):
        st.markdown(
            f"**Your listing:** {mine['title']} · {_layout(mine)} · rating "
            f"{mine['rating'] if pd.notna(mine['rating']) else '—'} "
            f"({int(mine['review_count'])} reviews)"
        )
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            nights = nights_picker(pricing["nights"].unique(), key="advisor_nights")
        max_km = c2.slider("Max distance (km)", 1, int(max(settings.search.radius_km, 1)),
                           min(3, int(settings.search.radius_km)), key="advisor_km")  # fmt: skip
        same_bedrooms = c3.toggle(
            f"Same bedrooms ({_int(mine['bedrooms'])})", value=True, key="advisor_bedrooms"
        )
        same_baths = c3.toggle(
            f"At least {_int(mine['bathrooms'])} bathrooms", value=True, key="advisor_baths"
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
            f"Only {len(comps)} comparables match. Widen the distance or relax the filters "
            f"(at least {analytics.MIN_COMPARABLES} are needed)."
        )
        return

    _headline(advice, currency)
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
            direction = "more" if gap > 0 else "less"
            st.caption(
                f":material/info: Your closest comparables charge **{abs(gap):.0f}% {direction}** "
                f"than the comp set overall (median {format_money(advice.median, currency)} vs "
                f"{format_money(market, currency)}): listings like yours (same bedrooms, "
                "bathrooms and area) are a different price tier. Relax the filters above to "
                "compare with the wider market."
            )
    if len(advice.comparables) < analytics.CONFIDENT_COMPARABLES:
        st.caption(
            f":orange[:material/warning: Only {len(advice.comparables)} comparables: a single "
            "listing moves the percentiles a lot. Widen the distance for a steadier estimate.]"
        )


def _headline(advice: analytics.PriceAdvice, currency: str) -> None:
    section(
        "Suggested rate",
        f"From {len(advice.comparables)} comparables, {advice.nights}-night stays.",
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Suggested nightly", format_money(advice.suggested, currency),
              help=f"Percentile {advice.target_percentile} of comparables")  # fmt: skip
    c2.metric(
        "Suggested monthly" if advice.monthly else "Comparables median",
        format_money(advice.monthly or advice.median, currency),
    )
    c3.metric(
        "Competitive range",
        f"{format_money(advice.p25, currency)} – {format_money(advice.p75, currency)}",
        help="P25–P75 of comparables: half of them charge within this range.",
    )
    position = (
        "upper half" if advice.target_percentile > 50
        else "lower half" if advice.target_percentile < 50 else "middle"
    )  # fmt: skip
    insight(
        f"Price around **{format_money(advice.suggested, currency)}** per night "
        + (f"(≈ **{format_money(advice.monthly, currency)}** per month) " if advice.monthly else "")
        + f"to sit in the **{position}** of your comparables (percentile "
        f"{advice.target_percentile}). Staying between {format_money(advice.p25, currency)} "
        f"and {format_money(advice.p75, currency)} keeps you competitive."
    )


def _percentile_explorer(advice: analytics.PriceAdvice, currency: str) -> None:
    section(
        "Explore other positions",
        "Pick a percentile to see the nightly price that puts you there: P20 means 20% of "
        "comparables are cheaper than you, P80 means 80% are.",
    )
    prices = advice.comparables["nightly"]
    chosen = st.slider(
        "Percentile",
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
    c1.metric(f"Nightly price at P{chosen}", format_money(price, currency),
              delta=f"{(price / advice.suggested - 1) * 100:+.0f}% vs suggested",
              delta_color="off")  # fmt: skip
    if advice.nights >= analytics.MONTHLY_STAY_NIGHTS:
        c2.metric("Monthly equivalent", format_money(price * 30, currency))
    c3.metric("Comparables cheaper than this", f"{cheaper} of {len(prices)}")

    st.altair_chart(_position_chart(advice, chosen, price, currency), width="stretch")

    ladder = pd.DataFrame(
        {
            "Percentile": [f"P{p}" for p in range(10, 100, 10)],
            "Nightly price": [round(float(prices.quantile(p / 100))) for p in range(10, 100, 10)],
        }
    )
    if advice.nights >= analytics.MONTHLY_STAY_NIGHTS:
        ladder["Monthly"] = ladder["Nightly price"] * 30
    with st.expander("Price at every 10th percentile"):
        amount = st.column_config.NumberColumn(format="localized")
        st.dataframe(ladder, hide_index=True, width="stretch",
                     column_config={"Nightly price": amount, "Monthly": amount})  # fmt: skip

    st.button(
        f"Calculate earnings at {format_money(price, currency)}",
        icon=":material/calculate:",
        on_click=_send_to_calculator,
        args=(round(price), advice.nights),
        help="Opens the earnings calculator with this price and stay length.",
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
            x=alt.X("nightly:Q", title=f"Nightly price ({currency})", scale=alt.Scale(zero=False)),
            tooltip=["title", alt.Tooltip("nightly:Q", format=",.0f")],
        )
    )
    markers = pd.DataFrame(
        {
            "price": [advice.suggested, price],
            "label": [f"▼ Suggested · P{advice.target_percentile}", f"▲ Your choice · P{chosen}"],
            "who": ["suggested", "chosen"],
        }
    )
    colors = alt.Scale(domain=["suggested", "chosen"], range=[CORAL, INK])
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
    st.session_state[CALCULATOR_PRICE] = price
    forget_money_input(CALCULATOR_PRICE)  # the formatted text is rebuilt from the new price
    st.session_state[CALCULATOR_NIGHTS] = nights
    st.session_state[_GO_TO_CALCULATOR] = True


def _signals(advice: analytics.PriceAdvice) -> None:
    section("Why this percentile",
            "Starts at the median (P50); each strength moves it up 8 percentiles, each "
            "weakness down 8, within P25–P75.")  # fmt: skip
    if not advice.signals:
        st.caption("Not enough information about your listing to adjust the median.")
        return
    table = pd.DataFrame(
        [
            {"Factor": s.factor, "Yours": s.yours, "Comparables (typical)": s.comparables,
             "Effect": _EFFECT[s.effect]}
            for s in advice.signals
        ]
    )  # fmt: skip
    st.dataframe(table, hide_index=True, width="stretch")
    caveat("Rules are simple and transparent on purpose: adjust them with your own judgement.")


def _seasonal(pricing: pd.DataFrame, advice: analytics.PriceAdvice, currency: str) -> None:
    table = analytics.seasonal_prices(pricing, advice.comparables, advice.nights,
                                      advice.suggested)  # fmt: skip
    if table.empty:
        return
    section(
        "Suggested rate by check-in date",
        "The same rate adjusted to how comparables price each check-in date (season factor "
        "= that date's median ÷ the overall median).",
    )
    amount = st.column_config.NumberColumn(format="localized")
    st.dataframe(
        table,
        width="stretch",
        column_config={
            "Comparables priced": st.column_config.NumberColumn(
                help="Comparables still available, and so priced, for that check-in."
            ),
            "Comparables median": amount,
            "Suggested nightly": amount,
            "Suggested monthly": amount,
        },
    )
    factors = analytics.by_label(table["Season factor"])
    high, low = max(factors, key=factors.__getitem__), min(factors, key=factors.__getitem__)
    insight(
        f"Check-ins around **{high}** support the highest rate "
        f"({factors[high]:.2f}× the median); around **{low}** the lowest ({factors[low]:.2f}×)."
    )
    caveat(
        "In busy periods the best listings are already booked, so the ones still available "
        "(and priced) are often cheaper. Few comparables priced on a date means a weaker signal."
    )


def _comparables_table(advice: analytics.PriceAdvice) -> None:
    with st.expander(f"The {len(advice.comparables)} comparables used"):
        view = advice.comparables[["title", "distance_km", "bedrooms", "bathrooms", "rating",
                                   "review_count", "nightly", "url"]].rename(
            columns={"title": "Listing", "distance_km": "Km", "bedrooms": "Bedrooms",
                     "bathrooms": "Baths", "rating": "Rating", "review_count": "Reviews",
                     "nightly": "Nightly", "url": "Airbnb"}
        )  # fmt: skip
        st.dataframe(
            view.sort_values("Nightly"),
            hide_index=True,
            width="stretch",
            column_config={
                "Nightly": st.column_config.NumberColumn(format="localized"),
                "Km": st.column_config.NumberColumn(format="%.1f"),
                "Airbnb": st.column_config.LinkColumn(display_text="Open"),
            },
        )
    how_to_read(
        "- **Comparables**: priced listings within the distance, with your bedrooms and at "
        "least your bathrooms (toggle to relax).\n"
        "- **Nightly** is each comparable's median across the check-in dates searched.\n"
        "- **Prices exclude cleaning fees and taxes**, like everywhere in this app."
    )


def _layout(mine: pd.Series) -> str:
    return f"{_int(mine['bedrooms'])} bedrooms · {_int(mine['bathrooms'])} baths"


def _int(value: object) -> str:
    number = analytics.as_float(value)
    return "—" if number is None else f"{number:g}"
