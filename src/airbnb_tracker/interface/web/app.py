"""Streamlit entry point. Start it with ``airbnb-tracker ui``."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import streamlit as st

from airbnb_tracker.interface.web import results_tab, run_tab, setup_tab
from airbnb_tracker.interface.web.theme import apply_page_style, header


def _config_path() -> Path:
    """``streamlit run app.py -- --config path`` (defaults like the CLI)."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=os.environ.get("AIRBNB_TRACKER_CONFIG", "config.yaml"))
    args, _unknown = parser.parse_known_args()
    return Path(args.config).resolve()


def main() -> None:
    st.set_page_config(page_title="Competitor Tracker", page_icon="🏠", layout="wide")
    apply_page_style()
    header("Competitor Tracker", "Prices, availability and amenities of the stays around yours.")

    config_path = _config_path()
    setup, run, results = st.tabs(["Search setup", "Run", "Results"])
    with setup:
        setup_tab.render(config_path)
    with run:
        run_tab.render(config_path)
    with results:
        results_tab.render(config_path)


main()
