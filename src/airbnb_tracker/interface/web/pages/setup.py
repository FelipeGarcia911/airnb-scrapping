"""Set up my market: which listing is yours and what counts as a comparable competitor."""

from __future__ import annotations

from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from airbnb_tracker.application.exceptions import GatewayError
from airbnb_tracker.domain.property_types import PropertyGroup, property_group
from airbnb_tracker.domain.value_objects import ListingId
from airbnb_tracker.infrastructure.airbnb import PyAirbnbDetailsGateway
from airbnb_tracker.infrastructure.config import (
    AppSettings,
    ConfigurationError,
    parse_settings,
    read_raw_config,
    save_raw_config,
)
from airbnb_tracker.interface.web.config_form import (
    DURATION_CHOICES,
    MONEY_FIELDS,
    OPTIONAL_NUMBERS,
    ROOM_TYPES,
    FormValues,
    from_raw,
    parse_listing_id,
    to_raw,
)
from airbnb_tracker.interface.web.context import config_path
from airbnb_tracker.interface.web.estimates import estimate
from airbnb_tracker.interface.web.formatting import forget_money_input, money_input
from airbnb_tracker.interface.web.i18n import t
from airbnb_tracker.interface.web.navigation import go_to
from airbnb_tracker.interface.web.theme import AREA_FILL

_PREFIX = "form_"
_FEEDBACK = "_locate_feedback"


def render() -> None:
    path = config_path()
    base = _load_base(path)
    _init_state(base)

    st.title(t("Set up my market"))
    st.caption(t("Setup & data · start here, then collect data"))
    st.markdown(
        t(
            "Tell the tracker **which listing is yours** and **what counts as a competitor**. "
            "Like a *comp set* in market tools, only comparable stays near yours are analysed. "
            "Changes take effect the next time you collect data (step 2)."
        )
    )
    _step_property()
    _step_area()
    _step_filters()
    _step_scenarios()
    _step_comp_set()
    _advanced()
    _summary_and_save(to_raw(_current_values(), base), path)


def _step_property() -> None:
    with st.container(border=True):
        st.subheader(t("1 · Your property"))
        st.caption(
            t(
                "Paste your listing and read it from Airbnb: its location centres the search. "
                "Then enter the pricing you use on Airbnb: it is the starting point of every "
                "simulation."
            )
        )
        st.text_input(
            t("Your Airbnb link or listing ID"),
            key=_key("listing_id"),
            placeholder="https://www.airbnb.com/rooms/123456789",
            help=t(
                "Open your listing on airbnb.com and copy the address bar. The number after "
                "/rooms/ is enough."
            ),
        )
        st.button(
            t("Read my listing from Airbnb"),
            icon=":material/download:",
            help=t(
                "Reads your listing's location (to centre the search) and kind of property "
                "(to compare like with like)."
            ),
            on_click=_read_listing,  # callbacks run before widgets, so they may update them
        )
        _show_locate_feedback()

        st.caption(t("Your rate, discounts and fees are in Settings → Listing configuration."))


def _step_area() -> None:
    with st.container(border=True):
        st.subheader(t("2 · Area"))
        st.caption(t("Where your guests would also look. Listings outside this area are ignored."))
        st.toggle(
            t("Use an Airbnb search URL instead of a point and radius"),
            key=_key("use_url"),
            help=t(
                "Advanced: search on airbnb.com with any filters, copy the URL and paste it "
                "here. Dates in the URL are replaced automatically."
            ),
        )
        if st.session_state[_key("use_url")]:
            st.text_input(t("Airbnb search URL"), key=_key("url"))
            return
        lat, lon, radius = st.columns(3)
        lat.number_input(t("Latitude"), key=_key("latitude"), format="%.5f", step=0.001)
        lon.number_input(t("Longitude"), key=_key("longitude"), format="%.5f", step=0.001)
        radius.slider(
            t("Radius (km)"),
            1,
            15,
            key=_key("radius_km"),
            step=1,
            format="%d km",
            help=t("A smaller radius is faster and keeps competitors truly comparable."),
        )
        st.map(
            {"lat": [st.session_state[_key("latitude")]],
             "lon": [st.session_state[_key("longitude")]]},
            zoom=12, height=220, color=AREA_FILL,
            size=st.session_state[_key("radius_km")] * 1000,
        )  # fmt: skip


def _step_filters() -> None:
    with st.container(border=True):
        st.subheader(t("3 · What is comparable"))
        st.caption(
            t(
                "The same filters a guest would use on Airbnb. Match them to your property. "
                "0 means no limit."
            )
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.number_input(
            t("Guests"), key=_key("guests"), min_value=1, max_value=16,
            help=t("Search for stays that fit this many guests."),
        )  # fmt: skip
        c2.selectbox(
            t("Type of place"), list(ROOM_TYPES), key=_key("room_type_label"), format_func=t
        )
        st.multiselect(
            t("Kinds of property to compare"),
            [g.value for g in PropertyGroup],
            key=_key("property_groups"),
            format_func=lambda value: t(PropertyGroup(value).label),
            placeholder=t("Every kind of property"),
            help=t(
                "A city apartment does not compete with a country estate. Pick the kind of "
                "your listing; leave empty to compare with everything."
            ),
        )
        c3.number_input(t("Min bedrooms"), key=_key("min_bedrooms"), min_value=0)
        c4.number_input(
            t("Max bedrooms"), key=_key("max_bedrooms"), min_value=0,
            help=t("Airbnb has no such filter: it is applied after searching."),
        )  # fmt: skip
        c1.number_input(t("Min bathrooms"), key=_key("min_bathrooms"), min_value=0)
        c2.number_input(t("Min beds"), key=_key("min_beds"), min_value=0)
        with c3:
            money_input(t("Min price / night"), key=_key("price_min"), currency=_currency(),
                        placeholder=t("No limit"), help=_price_range_help())  # fmt: skip
        with c4:
            money_input(t("Max price / night"), key=_key("price_max"), currency=_currency(),
                        placeholder=t("No limit"), help=_price_range_help())  # fmt: skip


def _price_range_help() -> str:
    return t(
        "Per night, for every stay length: applied to each price after reading its unit "
        "(Airbnb's own filter compares monthly prices from 28 nights on). Empty = no limit."
    )


def _step_scenarios() -> None:
    with st.container(border=True):
        st.subheader(t("4 · Stays to price"))
        st.caption(
            t(
                "Prices change with the length of the stay and the season. Each length is "
                "searched for every check-in date, so more choices mean longer updates."
            )
        )
        st.pills(
            t("Stay lengths (nights)"),
            DURATION_CHOICES,
            selection_mode="multi",
            key=_key("durations"),
            help=t("Pick the stays you sell: 28+ nights shows monthly discounts."),
        )
        c1, c2, c3 = st.columns(3)
        c1.number_input(
            t("First check-in in (days)"), key=_key("first_check_in_days"), min_value=0,
            help=t("Days from today to the first check-in date sampled."),
        )  # fmt: skip
        c2.number_input(
            t("One check-in every (days)"), key=_key("every_days"), min_value=1,
            help=t("30 = one check-in date per month."),
        )  # fmt: skip
        c3.number_input(
            t("Look ahead (days)"), key=_key("horizon_days"), min_value=1,
            help=t("How far into the future to sample. 180 = six months."),
        )  # fmt: skip


def _step_comp_set() -> None:
    with st.container(border=True):
        st.subheader(t("5 · Comp set size"))
        st.caption(
            t(
                "How many of the nearest matching listings to follow closely (amenities, "
                "availability, minimum stay, reviews)."
            )
        )
        c1, c2 = st.columns([1, 2])
        c1.number_input(
            t("Nearest competitors"), key=_key("competitors"), min_value=1, max_value=500,
            help=t("50 is a good balance between detail and update time."),
        )  # fmt: skip
        c2.toggle(
            t("Also keep prices of every listing in the area"),
            key=_key("keep_all_in_search"),
            help=t("Wider market percentiles, but only the nearest competitors get full details."),
        )


def _advanced() -> None:
    with st.expander(t("Advanced settings")):
        c1, c2, c3 = st.columns(3)
        c1.toggle(
            t("Complete coverage"), key=_key("complete_coverage"),
            help=t("Splits the area into smaller searches: ~15% more listings, ~2.5× slower."),
        )  # fmt: skip
        c2.text_input(t("Currency"), key=_key("currency"), max_chars=3)
        c3.number_input(
            t("Pause between requests (s)"), key=_key("min_delay_seconds"), min_value=1,
            step=1, help=t("Keeps usage moderate. Raise it if many requests fail."),
        )  # fmt: skip
        c1.number_input(
            t("Refresh listing pages every (days)"), key=_key("details_refresh_days"),
            min_value=1, help=t("Amenities and descriptions rarely change: weekly is enough."),
        )  # fmt: skip
        c2.number_input(
            t("Refresh calendars every (days)"), key=_key("calendar_refresh_days"), min_value=1,
            help=t("Availability changes fast: every 2–3 days builds a useful history."),
        )  # fmt: skip


def _summary_and_save(raw: dict[str, Any], path: Path) -> None:
    try:
        settings = parse_settings(raw, base_dir=path.resolve().parent)
    except ConfigurationError as error:
        st.error(t("Please fix this before saving: {error}", error=error), icon=":material/error:")
        return

    run = estimate(settings, len(settings.stays.resolve(date.today())))
    with st.container(border=True):
        st.subheader(t("What the next update will do"))
        c1, c2, c3 = st.columns(3)
        c1.metric(t("Price searches"), run.searches, help=t("Stay lengths × check-in dates"))
        c2.metric(t("Listings read in detail"), run.listings_to_enrich)
        c3.metric(t("Estimated time"), f"≈ {run.total_minutes:.0f} min")
        unsaved = _differs_from_saved(settings, path)
        if unsaved:
            st.caption(f":orange[{t('You have unsaved changes.')}]")
        go, save, discard = st.columns([2, 1, 3])
        if go.button(t("Save and continue"), type="primary", icon=":material/arrow_forward:"):
            save_raw_config(raw, path)
            go_to("collect")
        if save.button(t("Save"), icon=":material/save:", disabled=not unsaved):
            save_raw_config(raw, path)
            st.toast(t("Settings saved."), icon=":material/check_circle:")
            st.rerun()
        if discard.button(t("Discard changes"), type="tertiary", disabled=not unsaved):
            _reset_state()
            st.rerun()


def _read_listing() -> None:
    """Button callback: runs before the widgets are drawn, so it may change their values."""
    listing_id = parse_listing_id(st.session_state[_key("listing_id")] or "")
    if not listing_id:
        _set_feedback("warning", t("Paste your Airbnb link or listing ID first."))
        return
    try:
        details = PyAirbnbDetailsGateway().get_details(ListingId(listing_id))
    except GatewayError as error:
        _set_feedback("error", t("Could not read the listing: {error}", error=error))
        return
    st.session_state[_key("listing_id")] = listing_id
    messages = []
    location = details.listing.location
    if location is not None:
        st.session_state[_key("latitude")] = location.latitude
        st.session_state[_key("longitude")] = location.longitude
        st.session_state[_key("use_url")] = False
        messages.append(t("search centred on it"))
    group = property_group(details.listing.property_type)
    if group is not PropertyGroup.OTHER:
        st.session_state[_key("property_groups")] = [group.value]
        messages.append(t("comparing with: {kind}", kind=t(group.label)))
    _set_feedback("success", f"“{details.listing.name}”: " + "; ".join(messages) + ".")


def _set_feedback(kind: str, message: str) -> None:
    st.session_state[_FEEDBACK] = (kind, message)


def _show_locate_feedback() -> None:
    feedback = st.session_state.pop(_FEEDBACK, None)
    if feedback is None:
        return
    kind, message = feedback
    {"success": st.success, "warning": st.warning, "error": st.error}[kind](message)


def _load_base(path: Path) -> dict[str, Any]:
    for candidate in (path, path.parent / "config.example.yaml"):
        try:
            return read_raw_config(candidate)
        except ConfigurationError:
            continue
    return {}


def _key(name: str) -> str:
    return _PREFIX + name


def _init_state(base: dict[str, Any]) -> None:
    """Load the form from the file when its widgets are not in the session yet.

    Streamlit drops widget values when you leave a page, so this also reloads them
    (from the saved file) when you come back.
    """
    if _key("guests") in st.session_state:
        return
    values = from_raw(base)
    for field in fields(FormValues):
        value = getattr(values, field.name)
        if field.name in OPTIONAL_NUMBERS and value is None:
            value = 0  # number widgets show 0 = no limit
        is_list = field.name in {"durations", "property_groups"}
        st.session_state[_key(field.name)] = list(value) if is_list else value
    for name in MONEY_FIELDS:
        forget_money_input(_key(name))  # rebuild the formatted text from the loaded value


def _reset_state() -> None:
    for field in fields(FormValues):
        st.session_state.pop(_key(field.name), None)
    for name in MONEY_FIELDS:
        forget_money_input(_key(name))


def _currency() -> str:
    return str(st.session_state.get(_key("currency")) or "COP")


def _current_values() -> FormValues:
    values = {f.name: st.session_state[_key(f.name)] for f in fields(FormValues)}
    values["durations"] = tuple(values["durations"] or ())
    values["property_groups"] = tuple(values["property_groups"] or ())
    for name in OPTIONAL_NUMBERS:
        values[name] = values[name] or None  # 0 = no limit
    return FormValues(**values)


def _differs_from_saved(settings: AppSettings, path: Path) -> bool:
    """Compare meanings, not text: a missing key and its default value are the same."""
    try:
        saved = parse_settings(_load_base(path), base_dir=path.resolve().parent)
    except ConfigurationError:
        return True
    return settings != saved
