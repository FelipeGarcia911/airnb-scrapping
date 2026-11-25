"""Setup tab: edit location, filters and scenarios, then save ``config.yaml``."""

from __future__ import annotations

from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from airbnb_tracker.application.exceptions import GatewayError
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
    OPTIONAL_NUMBERS,
    ROOM_TYPES,
    FormValues,
    from_raw,
    parse_listing_id,
    to_raw,
)
from airbnb_tracker.interface.web.estimates import estimate

_PREFIX = "form_"


def render(config_path: Path) -> None:
    base = _load_base(config_path)
    _init_state(base)
    _listing_section()
    _location_section()
    _filters_section()
    _scenarios_section()
    _competitors_section()
    _advanced_section()
    _preview_and_save(to_raw(_current_values(), base), config_path)


def _listing_section() -> None:
    with st.container(border=True):
        st.subheader("Your listing")
        left, right = st.columns([3, 1])
        left.text_input(
            "Airbnb link or listing ID",
            key=_key("listing_id"),
            placeholder="https://www.airbnb.com/rooms/123456789",
        )
        right.number_input(
            "Your nightly price", key=_key("nightly_price"), min_value=0.0, step=5000.0,
            help="0 = not set",
        )  # fmt: skip
        if st.button("Use my listing's location", icon=":material/my_location:"):
            _locate_listing()


def _location_section() -> None:
    with st.container(border=True):
        st.subheader("Where")
        st.toggle("Use an Airbnb search URL instead of a point and radius", key=_key("use_url"))
        if st.session_state[_key("use_url")]:
            st.text_input(
                "Search URL",
                key=_key("url"),
                help="Search on airbnb.com with your filters and paste the URL. "
                "Dates are replaced for every scenario.",
            )
        else:
            lat, lon, radius = st.columns(3)
            lat.number_input("Latitude", key=_key("latitude"), format="%.5f", step=0.001)
            lon.number_input("Longitude", key=_key("longitude"), format="%.5f", step=0.001)
            radius.slider("Radius (km)", 0.5, 15.0, key=_key("radius_km"), step=0.5)
            st.map(
                {"lat": [st.session_state[_key("latitude")]],
                 "lon": [st.session_state[_key("longitude")]]},
                zoom=12,
                height=220,
                color="#FF385C",
                size=st.session_state[_key("radius_km")] * 1000,
            )  # fmt: skip


def _filters_section() -> None:
    with st.container(border=True):
        st.subheader("Filters")
        c1, c2, c3, c4 = st.columns(4)
        c1.number_input("Guests", key=_key("guests"), min_value=1, max_value=16)
        c2.selectbox("Type of place", list(ROOM_TYPES), key=_key("room_type_label"))
        c3.number_input("Min bedrooms", key=_key("min_bedrooms"), min_value=0)
        c4.number_input("Max bedrooms", key=_key("max_bedrooms"), min_value=0)
        c1.number_input("Min bathrooms", key=_key("min_bathrooms"), min_value=0)
        c2.number_input("Min beds", key=_key("min_beds"), min_value=0)
        c3.number_input("Min price / night", key=_key("price_min"), min_value=0, step=10000)
        c4.number_input("Max price / night", key=_key("price_max"), min_value=0, step=10000)
        st.caption("0 means no limit.")


def _scenarios_section() -> None:
    with st.container(border=True):
        st.subheader("Stay scenarios")
        st.pills(
            "Stay lengths (nights)",
            DURATION_CHOICES,
            selection_mode="multi",
            key=_key("durations"),
        )
        c1, c2, c3 = st.columns(3)
        c1.number_input("First check-in in (days)", key=_key("first_check_in_days"), min_value=0)
        c2.number_input("A check-in every (days)", key=_key("every_days"), min_value=1)
        c3.number_input("Look ahead (days)", key=_key("horizon_days"), min_value=1)


def _competitors_section() -> None:
    with st.container(border=True):
        st.subheader("Competitors")
        c1, c2 = st.columns([1, 2])
        c1.number_input(
            "Nearest competitors to analyse", key=_key("competitors"), min_value=1, max_value=500
        )
        c2.toggle(
            "Keep every listing in price searches (wider market percentiles)",
            key=_key("keep_all_in_search"),
        )


def _advanced_section() -> None:
    with st.expander("Advanced"):
        c1, c2, c3 = st.columns(3)
        c1.toggle("Complete coverage (slower, ~15% more listings)", key=_key("complete_coverage"))
        c2.text_input("Currency", key=_key("currency"), max_chars=3)
        c3.number_input(
            "Pause between requests (s)", key=_key("min_delay_seconds"), min_value=1.0, step=0.5
        )
        c1.number_input("Refresh listing pages every (days)", key=_key("details_refresh_days"))
        c2.number_input("Refresh calendars every (days)", key=_key("calendar_refresh_days"))


def _preview_and_save(raw: dict[str, Any], config_path: Path) -> None:
    try:
        settings = parse_settings(raw, base_dir=config_path.resolve().parent)
    except ConfigurationError as error:
        st.error(str(error), icon=":material/error:")
        return

    stays = settings.stays.resolve(date.today())
    run = estimate(settings, len(stays))
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Price searches", run.searches)
        c2.metric("Listings to enrich", run.listings_to_enrich)
        c3.metric("Scan time", f"≈ {run.scan_minutes:.0f} min")
        c4.metric("Enrich time", f"≈ {run.enrich_minutes:.0f} min")
        save, reload = st.columns([1, 5])
        if save.button("Save", type="primary", icon=":material/save:"):
            save_raw_config(raw, config_path)
            st.toast(f"Saved {config_path.name}", icon=":material/check_circle:")
        if reload.button("Discard changes", type="tertiary"):
            _reset_state()
            st.rerun()


def _locate_listing() -> None:
    listing_id = parse_listing_id(st.session_state[_key("listing_id")] or "")
    if not listing_id:
        st.warning("Paste your Airbnb link or listing ID first.")
        return
    with st.spinner("Reading your listing…"):
        try:
            details = PyAirbnbDetailsGateway().get_details(ListingId(listing_id))
        except GatewayError as error:
            st.error(f"Could not read the listing: {error}")
            return
    location = details.listing.location
    if location is None:
        st.warning("The listing has no public location.")
        return
    st.session_state[_key("listing_id")] = listing_id
    st.session_state[_key("latitude")] = location.latitude
    st.session_state[_key("longitude")] = location.longitude
    st.session_state[_key("use_url")] = False
    st.toast(f"Centered on “{details.listing.name}”", icon=":material/location_on:")
    st.rerun()


def _load_base(config_path: Path) -> dict[str, Any]:
    for candidate in (config_path, config_path.parent / "config.example.yaml"):
        try:
            return read_raw_config(candidate)
        except ConfigurationError:
            continue
    return {}


def _key(name: str) -> str:
    return _PREFIX + name


def _init_state(base: dict[str, Any]) -> None:
    if st.session_state.get(_key("loaded")):
        return
    values = from_raw(base)
    for field in fields(FormValues):
        value = getattr(values, field.name)
        if field.name in OPTIONAL_NUMBERS and value is None:
            value = 0.0 if field.name == "nightly_price" else 0  # widgets show 0 = no limit
        st.session_state[_key(field.name)] = list(value) if field.name == "durations" else value
    st.session_state[_key("loaded")] = True


def _reset_state() -> None:
    st.session_state[_key("loaded")] = False


def _current_values() -> FormValues:
    values = {f.name: st.session_state[_key(f.name)] for f in fields(FormValues)}
    values["durations"] = tuple(values["durations"] or ())
    for name in OPTIONAL_NUMBERS:
        values[name] = values[name] or None  # 0 = no limit
    return FormValues(**values)
