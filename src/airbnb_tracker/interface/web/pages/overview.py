"""Overview: the key numbers of your comp set and what they mean, in plain words."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import DurationReport
from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.context import settings_or_none
from airbnb_tracker.interface.web.data import MarketData, load_market, money, my_price_input
from airbnb_tracker.interface.web.navigation import link, next_step
from airbnb_tracker.interface.web.status import run_estimate

POSITION_LABELS = {
    "below_market": "Below market",
    "competitive": "Competitive",
    "premium": "Premium",
}
MONTHLY_SPECIALIST_NIGHTS = 20


def render() -> None:
    st.title("Overview")
    st.caption("Step 3 of 4 · Results")
    settings = settings_or_none()
    if settings is None:
        _welcome()
        return
    st.caption(_comp_set_description(settings))

    price = my_price_input(settings)
    data = load_market(settings, price)
    if data is None or not data.has_prices:
        _waiting_for_data(settings)
        return

    focus = _focus_duration(data.bundle.durations)
    _kpis(settings, data, focus)
    _takeaways(settings, data, focus, price)
    _explore()
    next_step("export", "Next: export the data")


def _welcome() -> None:
    st.markdown(
        "Track what the stays around yours charge, how booked they are and what they offer, "
        "so you can price with data instead of guesses."
    )
    steps = st.columns(3)
    cards = [
        ("1 · Set up your market", "Paste your listing and choose what counts as a "
         "competitor: area, bedrooms, guests, stay lengths."),
        ("2 · Collect data", "One click collects prices for every stay length and "
         "date, plus competitors' availability, minimum stays and amenities."),
        ("3 · Read the insights", "See price ranges, discounts, occupancy and where your "
         "price stands, then download everything for deeper analysis."),
    ]  # fmt: skip
    for column, (title, text) in zip(steps, cards, strict=True):
        with column.container(border=True, height="stretch"):
            st.markdown(f"**{title}**")
            st.caption(text)
    link("setup", "Start: set up my market", ":material/arrow_forward:")


def _waiting_for_data(settings: AppSettings) -> None:
    run = run_estimate(settings)
    st.info(
        f"Your market is set up but there is no data for it yet. Collect it in step 2 "
        f"(≈ {run.total_minutes:.0f} min); this page fills in when it finishes.",
        icon=":material/sync:",
    )
    next_step("collect", "Go to step 2: collect data")


def _comp_set_description(settings: AppSettings) -> str:
    search, filters = settings.search, settings.search.filters
    bedrooms = ""
    if filters.min_bedrooms or search.max_bedrooms:
        low, high = filters.min_bedrooms, search.max_bedrooms
        bedrooms = f"{low}–{high} bedrooms" if low and high else f"{low or high} bedrooms"
    kind = {"entire_place": "entire places", "private_room": "private rooms"}.get(
        filters.room_type.value if filters.room_type else "", "stays"
    )
    where = f"within {search.radius_km:g} km of your listing" if search.center else "in your URL"
    nearest = settings.enrich.competitors or "all"
    return (
        f"Comp set: the {nearest} nearest {kind}"
        + (f" with {bedrooms}" if bedrooms else "")
        + f" for {filters.guests} guests, {where}."
    )


def _focus_duration(durations: tuple[DurationReport, ...]) -> DurationReport:
    """30 nights if available (monthly market), otherwise the longest priced stay."""
    priced = [d for d in durations if d.distribution]
    return next((d for d in priced if d.nights == 30), priced[-1] if priced else durations[-1])


def _kpis(settings: AppSettings, data: MarketData, focus: DurationReport) -> None:
    currency = settings.scraping.currency
    dist = focus.distribution
    competitors = data.analysed[~data.analysed["is_mine"]] if len(data.analysed) else data.analysed
    availability = data.bundle.availability
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Comp set", len(competitors), help="Competitors with full details")
    c2.metric(
        f"Typical price · {focus.nights} nights",
        money(float(dist.median.amount), currency) if dist else "—",
        help="Median nightly price of the comp set",
    )
    position = (
        f"{POSITION_LABELS[focus.position.value]}"
        if focus.position and focus.percentile is not None
        else "—"
    )
    c3.metric(
        "Your position",
        position,
        help="Below P25 = below market · P25–P75 = competitive · above P75 = premium",
    )
    c4.metric(
        "Nights not bookable",
        f"{availability.median_unavailable_pct:.0f}%"
        if availability.median_unavailable_pct is not None
        else "—",
        help="Median over the next days: a rough sign of occupancy.",
    )
    c5.metric(
        "Typical minimum stay",
        f"{availability.median_min_nights:g} nights"
        if availability.median_min_nights is not None
        else "—",
    )


def _takeaways(
    settings: AppSettings, data: MarketData, focus: DurationReport, price: float | None
) -> None:
    currency = settings.scraping.currency
    points: list[str] = []
    dist = focus.distribution
    if dist:
        low, high = float(dist.p25.amount), float(dist.p75.amount)
        points.append(
            f"For **{focus.nights}-night stays**, half of your competitors charge between "
            f"**{money(low, currency)}** and **{money(high, currency)}** per night"
            + (
                f" (≈ {money(low * 30, currency)}–{money(high * 30, currency)} a month)."
                if focus.nights >= 28
                else "."
            )
        )
    if focus.discounted_share_pct and focus.median_discount_pct:
        points.append(
            f"**{focus.discounted_share_pct:.0f}%** of them discount {focus.nights}-night stays, "
            f"typically by **{focus.median_discount_pct:.0f}%**."
        )
    if price and focus.percentile is not None and focus.position:
        points.append(
            f"At **{money(price, currency)}** per night you are more expensive than "
            f"**{focus.percentile:.0f}%** of the comp set: "
            f"**{POSITION_LABELS[focus.position.value].lower()}**."
        )
    elif not price:
        points.append("Enter your nightly price above to see where you stand.")
    availability = data.bundle.availability
    if availability.median_unavailable_pct is not None:
        points.append(
            f"In the next {data.bundle.availability_window_days} days about "
            f"**{availability.median_unavailable_pct:.0f}%** of competitors' nights are not "
            "bookable (bookings or blocks)."
        )
    specialists = _monthly_specialists(data.analysed)
    if specialists:
        points.append(
            f"**{specialists}** competitors only accept stays of "
            f"{MONTHLY_SPECIALIST_NIGHTS}+ nights: your direct monthly competition "
            "(see Availability)."
        )
    with st.container(border=True):
        st.subheader("Key takeaways")
        st.markdown("\n".join(f"- {p}" for p in points))


def _monthly_specialists(analysed: pd.DataFrame) -> int:
    if analysed.empty or "minimum_nights" not in analysed:
        return 0
    competitors = analysed[~analysed["is_mine"]]
    return int((competitors["minimum_nights"].fillna(0) >= MONTHLY_SPECIALIST_NIGHTS).sum())


def _explore() -> None:
    st.subheader("Explore")
    cards = [
        ("advisor", "Pricing advisor", ":material/price_check:",
         "What should I charge? A suggested rate from your closest comparables."),
        ("prices", "Prices", ":material/payments:",
         "Price ranges per stay length, long-stay discounts and seasonality."),
        ("demand", "Demand", ":material/trending_up:",
         "Which dates are filling up and which months are busy historically."),
        ("comp_set", "Comp set", ":material/holiday_village:",
         "Who the competitors are, and which amenities are worth more."),
        ("positioning", "Positioning", ":material/scatter_plot:",
         "Price versus quality: who is overpriced and who is good value."),
        ("availability", "Availability", ":material/calendar_month:",
         "How booked they are, minimum stays and monthly specialists."),
    ]  # fmt: skip
    for row in (cards[:3], cards[3:]):
        for column, (name, label, icon, text) in zip(st.columns(3), row, strict=True):
            with column.container(border=True, height="stretch"):
                link(name, label, icon)
                st.caption(text)
