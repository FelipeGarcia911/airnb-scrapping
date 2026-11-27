"""Overview: the key numbers of your comp set and what they mean, in plain words."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from airbnb_tracker.application.dto import DurationReport
from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.context import settings_or_none
from airbnb_tracker.interface.web.data import MarketData, load_market, money, my_price_input
from airbnb_tracker.interface.web.guards import outlier_notice
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.navigation import link, next_step
from airbnb_tracker.interface.web.status import run_estimate

POSITION_LABELS = {
    "below_market": "Below market",
    "competitive": "Competitive",
    "premium": "Premium",
}
POSITION_LABELS_LOWER = {
    "below_market": "below market",
    "competitive": "competitive",
    "premium": "premium",
}
MONTHLY_SPECIALIST_NIGHTS = 20


def render() -> None:
    st.title(t("Market overview"))
    st.caption(t("Market analytics"))
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

    outlier_notice(data)
    focus = _focus_duration(data.bundle.durations)
    _kpis(settings, data, focus)
    _takeaways(settings, data, focus, price)
    _explore()
    next_step("price_setup", t("Next: price & configure"))


def _welcome() -> None:
    st.markdown(
        t(
            "Track what the stays around yours charge, how booked they are and what they "
            "offer, so you can price with data instead of guesses."
        )
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
            st.markdown(f"**{t(title)}**")
            st.caption(t(text))
    link("setup", t("Start: set up my market"), ":material/arrow_forward:")


def _waiting_for_data(settings: AppSettings) -> None:
    run = run_estimate(settings)
    st.info(
        t(
            "Your market is set up but there is no data for it yet. Collect it in step 2 "
            "(≈ {minutes:.0f} min); this page fills in when it finishes.",
            minutes=run.total_minutes,
        ),
        icon=":material/sync:",
    )
    next_step("collect", t("Collect data"))


def _comp_set_description(settings: AppSettings) -> str:
    search, filters = settings.search, settings.search.filters
    bedrooms = ""
    if filters.min_bedrooms or search.max_bedrooms:
        low, high = filters.min_bedrooms, search.max_bedrooms
        bedrooms = (
            t("{low}–{high} bedrooms", low=low, high=high)
            if low and high
            else t("{count} bedrooms", count=low or high)
        )
    kind = {
        "entire_place": t("entire places"),
        "private_room": t("private rooms"),
    }.get(filters.room_type.value if filters.room_type else "", t("stays"))
    where = (
        t("within {km:g} km of your listing", km=search.radius_km)
        if search.center
        else t("in your URL")
    )
    nearest = settings.enrich.competitors or t("all")
    description = t("Comp set: the {nearest} nearest {kind}", nearest=nearest, kind=kind)
    if bedrooms:
        description += t(" with {bedrooms}", bedrooms=bedrooms)
    return description + t(" for {guests} guests, {where}.", guests=filters.guests, where=where)


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
    c1.metric(t("Comp set"), len(competitors), help=t("Competitors with full details"))
    c2.metric(
        t("Typical nightly after discount · {nights} nights", nights=focus.nights),
        money(float(dist.median.amount), currency) if dist else "—",
        help=t("Median nightly price of the comp set"),
    )
    position = (
        t(POSITION_LABELS[focus.position.value])
        if focus.position and focus.percentile is not None
        else "—"
    )
    c3.metric(
        t("Your position"),
        position,
        help=t("Below P25 = below market · P25–P75 = competitive · above P75 = premium"),
    )
    c4.metric(
        t("Nights not bookable"),
        f"{availability.median_unavailable_pct:.0f}%"
        if availability.median_unavailable_pct is not None
        else "—",
        help=t("Median over the next days: a rough sign of occupancy."),
    )
    c5.metric(
        t("Typical minimum stay"),
        t("{nights:g} nights", nights=availability.median_min_nights)
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
        if focus.nights >= 28:
            points.append(
                t(
                    "For **{nights}-night stays**, half of your competitors charge between "
                    "**{low}** and **{high}** per night (≈ {low_month}–{high_month} a month).",
                    nights=focus.nights,
                    low=money(low, currency),
                    high=money(high, currency),
                    low_month=money(low * 30, currency),
                    high_month=money(high * 30, currency),
                )
            )
        else:
            points.append(
                t(
                    "For **{nights}-night stays**, half of your competitors charge between "
                    "**{low}** and **{high}** per night.",
                    nights=focus.nights,
                    low=money(low, currency),
                    high=money(high, currency),
                )
            )
    if focus.discounted_share_pct and focus.median_discount_pct:
        points.append(
            t(
                "**{share:.0f}%** of them discount {nights}-night stays, typically by "
                "**{discount:.0f}%**.",
                share=focus.discounted_share_pct,
                nights=focus.nights,
                discount=focus.median_discount_pct,
            )
        )
    if price and focus.percentile is not None and focus.position:
        points.append(
            t(
                "At **{price}** per night you are more expensive than **{percentile:.0f}%** "
                "of the comp set: **{position}**.",
                price=money(price, currency),
                percentile=focus.percentile,
                position=t(POSITION_LABELS_LOWER[focus.position.value]),
            )
        )
    elif not price:
        points.append(t("Enter your nightly price above to see where you stand."))
    availability = data.bundle.availability
    if availability.median_unavailable_pct is not None:
        points.append(
            t(
                "In the next {days} days about **{pct:.0f}%** of competitors' nights are not "
                "bookable (bookings or blocks).",
                days=data.bundle.availability_window_days,
                pct=availability.median_unavailable_pct,
            )
        )
    specialists = _monthly_specialists(data.analysed)
    if specialists:
        points.append(
            t(
                "**{count}** competitors only accept stays of {nights}+ nights: your direct "
                "monthly competition (see Availability).",
                count=specialists,
                nights=MONTHLY_SPECIALIST_NIGHTS,
            )
        )
    with st.container(border=True):
        st.subheader(t("Key takeaways"))
        st.markdown("\n".join(f"- {p}" for p in points))


def _monthly_specialists(analysed: pd.DataFrame) -> int:
    if analysed.empty or "minimum_nights" not in analysed:
        return 0
    competitors = analysed[~analysed["is_mine"]]
    return int((competitors["minimum_nights"].fillna(0) >= MONTHLY_SPECIALIST_NIGHTS).sum())


def _explore() -> None:
    groups = [
        ("Market analytics", [
            ("comp_set", "Comparables & amenities", ":material/holiday_village:",
             "Who your competitors are and which amenities are worth more."),
            ("prices", "Prices", ":material/payments:",
             "What they charge per stay length, long-stay discounts and seasonality."),
            ("availability", "Observed availability", ":material/calendar_month:",
             "Competitors' calendars, minimum stays and past activity from reviews."),
            ("positioning", "Positioning", ":material/scatter_plot:",
             "Price versus quality: who is overpriced and who is good value."),
        ]),
        ("Pricing", [
            ("price_setup", "Price & configure", ":material/tune:",
             "Your price by check-in date, a simulator and the values to enter in Airbnb."),
            ("advisor", "Pricing advisor", ":material/price_check:",
             "What to charge: a suggested rate and any percentile you choose."),
            ("earnings", "Earnings calculator", ":material/calculate:",
             "What a price really earns after Airbnb's fees, taxes and your costs."),
        ]),
    ]  # fmt: skip
    for title, cards in groups:
        st.subheader(t(title))
        for start in range(0, len(cards), 3):
            row = cards[start : start + 3]
            for column, (name, label, icon, text) in zip(st.columns(3), row, strict=False):
                with column.container(border=True, height="stretch"):
                    link(name, t(label), icon)
                    st.caption(t(text))
