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
from airbnb_tracker.interface.web.navigation import go_to
from airbnb_tracker.interface.web.theme import AREA_FILL

_PREFIX = "form_"
_FEEDBACK = "_locate_feedback"


def render() -> None:
    path = config_path()
    base = _load_base(path)
    _init_state(base)

    st.title("Set up my market")
    st.caption(
        "Step 1 of 5 · then collect data, analyse the market, price your listing and export."
    )
    st.markdown(
        "Tell the tracker **which listing is yours** and **what counts as a competitor**. "
        "Like a *comp set* in market tools, only comparable stays near yours are analysed. "
        "Changes take effect the next time you collect data (step 2)."
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
        st.subheader("1 · Your property")
        st.caption(
            "Paste your listing so it is included in every analysis and the search can be "
            "centred on it."
        )
        left, right = st.columns([3, 1])
        left.text_input(
            "Your Airbnb link or listing ID",
            key=_key("listing_id"),
            placeholder="https://www.airbnb.com/rooms/123456789",
            help="Open your listing on airbnb.com and copy the address bar. The number after "
            "/rooms/ is enough.",
        )
        with right:
            money_input(
                "Your nightly price",
                key=_key("nightly_price"),
                currency=_currency(),
                help="What you charge (or plan to charge) per night. Used to show your "
                "position. Leave empty if not set.",
            )
        st.button(
            "Centre the search on my listing",
            icon=":material/my_location:",
            help="Reads your listing's location from Airbnb and uses it as the search centre.",
            on_click=_locate_listing,  # callbacks run before widgets, so they may update them
        )
        _show_locate_feedback()


def _step_area() -> None:
    with st.container(border=True):
        st.subheader("2 · Area")
        st.caption("Where your guests would also look. Listings outside this area are ignored.")
        st.toggle(
            "Use an Airbnb search URL instead of a point and radius",
            key=_key("use_url"),
            help="Advanced: search on airbnb.com with any filters, copy the URL and paste it "
            "here. Dates in the URL are replaced automatically.",
        )
        if st.session_state[_key("use_url")]:
            st.text_input("Airbnb search URL", key=_key("url"))
            return
        lat, lon, radius = st.columns(3)
        lat.number_input("Latitude", key=_key("latitude"), format="%.5f", step=0.001)
        lon.number_input("Longitude", key=_key("longitude"), format="%.5f", step=0.001)
        radius.slider(
            "Radius (km)",
            1,
            15,
            key=_key("radius_km"),
            step=1,
            format="%d km",
            help="A smaller radius is faster and keeps competitors truly comparable.",
        )
        st.map(
            {"lat": [st.session_state[_key("latitude")]],
             "lon": [st.session_state[_key("longitude")]]},
            zoom=12, height=220, color=AREA_FILL,
            size=st.session_state[_key("radius_km")] * 1000,
        )  # fmt: skip


def _step_filters() -> None:
    with st.container(border=True):
        st.subheader("3 · What is comparable")
        st.caption(
            "The same filters a guest would use on Airbnb. Match them to your property. "
            "0 means no limit."
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.number_input(
            "Guests", key=_key("guests"), min_value=1, max_value=16,
            help="Search for stays that fit this many guests.",
        )  # fmt: skip
        c2.selectbox("Type of place", list(ROOM_TYPES), key=_key("room_type_label"))
        st.multiselect(
            "Kinds of property to compare",
            [g.value for g in PropertyGroup],
            key=_key("property_groups"),
            format_func=lambda value: PropertyGroup(value).label,
            placeholder="Every kind of property",
            help="A city apartment does not compete with a country estate. Pick the kind of "
            "your listing; leave empty to compare with everything.",
        )
        c3.number_input("Min bedrooms", key=_key("min_bedrooms"), min_value=0)
        c4.number_input(
            "Max bedrooms", key=_key("max_bedrooms"), min_value=0,
            help="Airbnb has no such filter: it is applied after searching.",
        )  # fmt: skip
        c1.number_input("Min bathrooms", key=_key("min_bathrooms"), min_value=0)
        c2.number_input("Min beds", key=_key("min_beds"), min_value=0)
        with c3:
            money_input("Min price / night", key=_key("price_min"), currency=_currency(),
                        placeholder="No limit")  # fmt: skip
        with c4:
            money_input("Max price / night", key=_key("price_max"), currency=_currency(),
                        placeholder="No limit")  # fmt: skip


def _step_scenarios() -> None:
    with st.container(border=True):
        st.subheader("4 · Stays to price")
        st.caption(
            "Prices change with the length of the stay and the season. Each length is "
            "searched for every check-in date, so more choices mean longer updates."
        )
        st.pills(
            "Stay lengths (nights)",
            DURATION_CHOICES,
            selection_mode="multi",
            key=_key("durations"),
            help="Pick the stays you sell: 28+ nights shows monthly discounts.",
        )
        c1, c2, c3 = st.columns(3)
        c1.number_input(
            "First check-in in (days)", key=_key("first_check_in_days"), min_value=0,
            help="Days from today to the first check-in date sampled.",
        )  # fmt: skip
        c2.number_input(
            "One check-in every (days)", key=_key("every_days"), min_value=1,
            help="30 = one check-in date per month.",
        )  # fmt: skip
        c3.number_input(
            "Look ahead (days)", key=_key("horizon_days"), min_value=1,
            help="How far into the future to sample. 180 = six months.",
        )  # fmt: skip


def _step_comp_set() -> None:
    with st.container(border=True):
        st.subheader("5 · Comp set size")
        st.caption(
            "How many of the nearest matching listings to follow closely (amenities, "
            "availability, minimum stay, reviews)."
        )
        c1, c2 = st.columns([1, 2])
        c1.number_input(
            "Nearest competitors", key=_key("competitors"), min_value=1, max_value=500,
            help="50 is a good balance between detail and update time.",
        )  # fmt: skip
        c2.toggle(
            "Also keep prices of every listing in the area",
            key=_key("keep_all_in_search"),
            help="Wider market percentiles, but only the nearest competitors get full details.",
        )


def _advanced() -> None:
    with st.expander("Advanced settings"):
        c1, c2, c3 = st.columns(3)
        c1.toggle(
            "Complete coverage", key=_key("complete_coverage"),
            help="Splits the area into smaller searches: ~15% more listings, ~2.5× slower.",
        )  # fmt: skip
        c2.text_input("Currency", key=_key("currency"), max_chars=3)
        c3.number_input(
            "Pause between requests (s)", key=_key("min_delay_seconds"), min_value=1,
            step=1, help="Keeps usage moderate. Raise it if many requests fail.",
        )  # fmt: skip
        c1.number_input(
            "Refresh listing pages every (days)", key=_key("details_refresh_days"), min_value=1,
            help="Amenities and descriptions rarely change: weekly is enough.",
        )  # fmt: skip
        c2.number_input(
            "Refresh calendars every (days)", key=_key("calendar_refresh_days"), min_value=1,
            help="Availability changes fast: every 2–3 days builds a useful history.",
        )  # fmt: skip


def _summary_and_save(raw: dict[str, Any], path: Path) -> None:
    try:
        settings = parse_settings(raw, base_dir=path.resolve().parent)
    except ConfigurationError as error:
        st.error(f"Please fix this before saving: {error}", icon=":material/error:")
        return

    run = estimate(settings, len(settings.stays.resolve(date.today())))
    with st.container(border=True):
        st.subheader("What the next update will do")
        c1, c2, c3 = st.columns(3)
        c1.metric("Price searches", run.searches, help="Stay lengths × check-in dates")
        c2.metric("Listings read in detail", run.listings_to_enrich)
        c3.metric("Estimated time", f"≈ {run.total_minutes:.0f} min")
        unsaved = raw != _load_base(path)
        if unsaved:
            st.caption(":orange[You have unsaved changes.]")
        go, save, discard = st.columns([2, 1, 3])
        if go.button("Save and continue", type="primary", icon=":material/arrow_forward:"):
            save_raw_config(raw, path)
            go_to("collect")
        if save.button("Save", icon=":material/save:", disabled=not unsaved):
            save_raw_config(raw, path)
            st.toast("Settings saved.", icon=":material/check_circle:")
            st.rerun()
        if discard.button("Discard changes", type="tertiary", disabled=not unsaved):
            _reset_state()
            st.rerun()


def _locate_listing() -> None:
    """Button callback: runs before the widgets are drawn, so it may change their values."""
    listing_id = parse_listing_id(st.session_state[_key("listing_id")] or "")
    if not listing_id:
        _set_feedback("warning", "Paste your Airbnb link or listing ID first.")
        return
    try:
        details = PyAirbnbDetailsGateway().get_details(ListingId(listing_id))
    except GatewayError as error:
        _set_feedback("error", f"Could not read the listing: {error}")
        return
    location = details.listing.location
    if location is None:
        _set_feedback("warning", "The listing has no public location.")
        return
    st.session_state[_key("listing_id")] = listing_id
    st.session_state[_key("latitude")] = location.latitude
    st.session_state[_key("longitude")] = location.longitude
    st.session_state[_key("use_url")] = False
    group = property_group(details.listing.property_type)
    if group is not PropertyGroup.OTHER:
        st.session_state[_key("property_groups")] = [group.value]
    _set_feedback(
        "success",
        f"Search centred on “{details.listing.name}”; comparing with {group.value}s.",
    )


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
