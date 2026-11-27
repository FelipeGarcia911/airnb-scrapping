"""Settings: how the app looks and behaves, and how your listing is priced on Airbnb.

Two independent sections, each with its own save button:

* **App preferences** (``ui`` in config.yaml): language, regional format, time zone,
  start page, remembering the selection, export format, theme and density.
* **Listing configuration** (``my_listing`` and ``calculator``): base rate, discounts,
  cleaning fee, minimum stay and Airbnb's fee and taxes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import streamlit as st

from airbnb_tracker.infrastructure.config import (
    ConfigurationError,
    read_raw_config,
    save_raw_config,
)
from airbnb_tracker.interface.web.config_form import DURATION_CHOICES
from airbnb_tracker.interface.web.context import config_path, settings_or_none
from airbnb_tracker.interface.web.formatting import forget_money_input, money_input
from airbnb_tracker.interface.web.i18n import Language, current_language, set_language, t
from airbnb_tracker.interface.web.listing_config import STAY_VAT_PCT, ListingConfiguration
from airbnb_tracker.interface.web.preferences import (
    Density,
    Preferences,
    Region,
    StartPage,
    Theme,
    preferences,
    save_preferences,
)
from airbnb_tracker.interface.web.regional import long_date, number

_TIMEZONES = (
    "America/Bogota",
    "America/Lima",
    "America/Mexico_City",
    "America/New_York",
    "Europe/Madrid",
    "UTC",
)
_START_PAGES = {
    StartPage.PRICING: "Price & configure",
    StartPage.ADVISOR: "Pricing advisor",
    StartPage.OVERVIEW: "Market overview",
}
_SEPARATORS = {";": "Semicolon (;): Excel in Spanish", ",": "Comma (,)"}
_DATE_FORMATS = {"%Y-%m-%d": "YYYY-MM-DD", "%d/%m/%Y": "DD/MM/YYYY"}
_THEMES = {Theme.SYSTEM: "System", Theme.LIGHT: "Light", Theme.DARK: "Dark"}
_DENSITIES = {Density.COMFORTABLE: "Comfortable", Density.COMPACT: "Compact"}
_LISTING_KEY = "_settings_listing_"


def render() -> None:
    st.title(t("Settings"))
    st.markdown(t("Saved in config.yaml. They change how things are shown, never your data."))
    tab_app, tab_listing = st.tabs([t("App preferences"), t("Listing configuration")])
    with tab_app:
        _language()
        _app_preferences()
    with tab_listing:
        _listing_configuration()


# --- app preferences -------------------------------------------------------------------


def _language() -> None:
    with st.container(border=True):
        st.subheader(t("Language"))
        st.caption(t("Language of every screen. Data from Airbnb keeps its original language."))
        choice = st.segmented_control(
            t("Language"),
            list(Language),
            default=current_language(),
            format_func=lambda language: language.label,
            label_visibility="collapsed",
            key="_settings_language",
        )
        if choice is not None and choice is not current_language():
            set_language(Language(choice))
            st.rerun()


def _app_preferences() -> None:
    prefs = preferences()
    with st.form("app_preferences", border=True):
        st.subheader(t("Regional format and time"))
        c1, c2 = st.columns(2)
        region = c1.selectbox(
            t("Regional format"),
            list(Region),
            index=list(Region).index(prefs.region),
            format_func=_region_example,
            help=t("Separators and dates in every screen. Money keeps its currency code."),
        )
        zones = list(dict.fromkeys((prefs.timezone, *_TIMEZONES)))
        timezone = c2.selectbox(
            t("Time zone"),
            zones,
            index=0,
            help=t("For the last update, capture times and logs."),
        )

        st.subheader(t("Start"))
        c1, c2, c3 = st.columns(3)
        start_page = c1.selectbox(
            t("Start page"),
            list(_START_PAGES),
            index=list(_START_PAGES).index(prefs.start_page),
            format_func=lambda page: t(_START_PAGES[page]),
            help=t("Where the app opens once there is data."),
        )
        default_nights = c2.selectbox(
            t("Default stay length"),
            DURATION_CHOICES,
            index=_index(DURATION_CHOICES, prefs.default_nights),
            format_func=lambda n: t("{nights} nights", nights=n),
            help=t("Used when that length was collected; otherwise the longest one."),
        )
        remember_selection = c3.toggle(
            t("Remember the last selection"),
            value=prefs.remember_selection,
            help=t(
                "Reopens the pricing screen on your last check-in and comparable filters. If "
                "that date has passed or has no data, the next date with data is used."
            ),
        )

        st.subheader(t("Exports"))
        c1, c2 = st.columns(2)
        csv_separator = c1.selectbox(
            t("CSV separator"),
            list(_SEPARATORS),
            index=_index(list(_SEPARATORS), prefs.csv_separator),
            format_func=lambda sep: t(_SEPARATORS[sep]),
        )
        csv_date_format = c2.selectbox(
            t("Dates in CSV files"),
            list(_DATE_FORMATS),
            index=_index(list(_DATE_FORMATS), prefs.csv_date_format),
            format_func=lambda fmt: t(_DATE_FORMATS[fmt]),
            help=t("For downloads from Export. Timestamps stay in ISO 8601."),
        )

        st.subheader(t("Appearance"))
        c1, c2 = st.columns(2)
        theme = c1.segmented_control(
            t("Theme"),
            list(_THEMES),
            default=prefs.theme,
            format_func=lambda th: t(_THEMES[th]),
            help=t("Applies the next time you start the app (airbnb-tracker ui)."),
        )
        density = c2.segmented_control(
            t("Density"), list(_DENSITIES), default=prefs.density,
            format_func=lambda d: t(_DENSITIES[d]),
        )  # fmt: skip

        saved = st.form_submit_button(t("Save preferences"), type="primary", icon=":material/save:")
    if saved:
        updated = replace(
            prefs,
            region=Region(region or prefs.region),
            timezone=str(timezone),
            start_page=StartPage(start_page or prefs.start_page),
            default_nights=int(default_nights),
            remember_selection=bool(remember_selection),
            csv_separator=str(csv_separator),
            csv_date_format=str(csv_date_format),
            theme=Theme(theme or prefs.theme),
            density=Density(density or prefs.density),
        )
        save_preferences(updated)
        if updated.theme is not prefs.theme:
            st.session_state["_theme_restart"] = True
        st.toast(t("Preferences saved."), icon=":material/check_circle:")
        st.rerun()
    if st.session_state.get("_theme_restart"):
        st.info(t("Restart the app to apply the new theme."), icon=":material/restart_alt:")

    if st.button(
        t("Reset preferences"),
        icon=":material/restart_alt:",
        help=t("Back to the defaults for this section. Your language is kept."),
    ):
        save_preferences(Preferences())
        st.toast(t("Preferences reset."), icon=":material/check_circle:")
        st.rerun()


def _region_example(region: Region) -> str:
    example = f"${number(150000, 0, region)} COP · {long_date(date(2026, 11, 27), region)}"
    return f"{region.value} · {example}"


def _index(options: list[str] | tuple[int, ...], value: object) -> int:
    return list(options).index(value) if value in options else 0


# --- listing configuration --------------------------------------------------------------


def _listing_configuration() -> None:
    settings = settings_or_none()
    if settings is None:
        st.info(t("Set up your market first: this section is saved in its configuration."))
        return
    currency = settings.scraping.currency
    try:
        raw = read_raw_config(config_path())
    except ConfigurationError as error:
        st.error(str(error))
        return
    saved = ListingConfiguration.from_raw(raw)
    _seed(saved)

    with st.container(border=True):
        st.subheader(t("Your pricing on Airbnb"))
        st.caption(
            t(
                "What you have set in Airbnb today. Every pricing screen compares and "
                "simulates from these values."
            )
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            money_input(t("Base nightly rate"), key=_key("nightly_price"), currency=currency,
                        help=t("The nightly price you publish, before discounts."))  # fmt: skip
        c2.number_input(t("Weekly discount %"), 0, 99, key=_key("weekly_discount_pct"),
                        help=t("Applies from 7 nights."))  # fmt: skip
        c3.number_input(t("Monthly discount %"), 0, 99, key=_key("monthly_discount_pct"),
                        help=t("Applies from 28 nights."))  # fmt: skip
        c1, c2, _c3 = st.columns(3)
        with c1:
            money_input(t("Cleaning fee"), key=_key("cleaning_fee"), currency=currency)
        c2.number_input(t("Minimum stay (nights)"), 0, 365, key=_key("min_nights"),
                        help=t("0 = not set."))  # fmt: skip

    with st.container(border=True):
        st.subheader(t("Airbnb fees and taxes"))
        st.caption(t("Colombia defaults. Confirm them with your accountant."))
        c1, c2, c3 = st.columns(3)
        c1.number_input(t("Airbnb service fee %"), 0.0, 50.0, step=0.5,
                        key=_key("host_fee_pct"))  # fmt: skip
        c2.number_input(t("VAT on Airbnb's fee %"), 0.0, 30.0, step=1.0,
                        key=_key("vat_on_fee_pct"))  # fmt: skip
        c3.toggle(
            t("I charge VAT on the stay (responsable de IVA)"),
            key=_key("charges_stay_vat"),
            help=t("If so, {vat:g}% of what the guest pays is VAT you owe the DIAN.",
                   vat=STAY_VAT_PCT),
        )  # fmt: skip

    edited = _edited()
    if edited == saved:
        st.caption(t("No unsaved changes."))
        return
    st.caption(f":orange[{t('You have unsaved changes.')}]")
    c1, c2 = st.columns(2)
    if c1.button(t("Save listing configuration"), type="primary", icon=":material/save:"):
        save_raw_config(edited.apply_to(raw), config_path())
        st.cache_data.clear()
        st.toast(t("Saved. Apply the same values in Airbnb."), icon=":material/check_circle:")
        st.rerun()
    if c2.button(t("Discard changes"), icon=":material/undo:"):
        _seed(saved, force=True)
        st.rerun()


def _key(name: str) -> str:
    return f"{_LISTING_KEY}{name}"


def _seed(values: ListingConfiguration, *, force: bool = False) -> None:
    """Fill the widgets from the saved values the first time (or on discard)."""
    for name, value in vars(values).items():
        key = _key(name)
        if force or key not in st.session_state:
            st.session_state[key] = (value or 0) if name == "min_nights" else value
            forget_money_input(key)


def _edited() -> ListingConfiguration:
    state = st.session_state
    return ListingConfiguration(
        nightly_price=state[_key("nightly_price")] or None,
        weekly_discount_pct=int(state[_key("weekly_discount_pct")]),
        monthly_discount_pct=int(state[_key("monthly_discount_pct")]),
        cleaning_fee=state[_key("cleaning_fee")],
        min_nights=int(state[_key("min_nights")]) or None,
        host_fee_pct=float(state[_key("host_fee_pct")]),
        vat_on_fee_pct=float(state[_key("vat_on_fee_pct")]),
        charges_stay_vat=bool(state[_key("charges_stay_vat")]),
    )
