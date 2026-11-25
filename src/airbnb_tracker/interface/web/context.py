"""Shared page context: where the config lives and whether the app is set up."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from airbnb_tracker.infrastructure.config import AppSettings, ConfigurationError, load_settings


def config_path() -> Path:
    """``streamlit run app.py -- --config path`` (same default as the CLI)."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=os.environ.get("AIRBNB_TRACKER_CONFIG", "config.yaml"))
    args, _unknown = parser.parse_known_args()
    return Path(args.config).resolve()


def settings_or_none() -> AppSettings | None:
    try:
        return load_settings(config_path())
    except ConfigurationError:
        return None


def configuration_error() -> str | None:
    try:
        load_settings(config_path())
    except ConfigurationError as error:
        return str(error)
    return None
