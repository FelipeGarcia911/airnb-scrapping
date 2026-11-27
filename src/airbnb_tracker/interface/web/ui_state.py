"""The last selection on the pricing screen (stay length, check-in, comparable filters).

Kept in ``ui_state.json`` next to the database, not in config.yaml: config changes
reload the market data, and a selection changes on every click. Only used when
Settings → Remember the last selection is on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from airbnb_tracker.infrastructure.config import AppSettings
from airbnb_tracker.interface.web.preferences import preferences

_FILE_NAME = "ui_state.json"


class SelectionStore:
    """Repository of the last selection: a flat JSON object, rewritten only on change."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, **values: Any) -> None:
        current = self.load()
        updated = current | values
        if updated != current:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(updated, indent=2), encoding="utf-8")


def _store(settings: AppSettings) -> SelectionStore:
    return SelectionStore(settings.storage.database_path.parent / _FILE_NAME)


def remembered(settings: AppSettings) -> dict[str, Any]:
    """The last selection, or nothing when the preference is off."""
    return _store(settings).load() if preferences().remember_selection else {}


def remember(settings: AppSettings, **values: Any) -> None:
    if preferences().remember_selection:
        _store(settings).save(**values)
