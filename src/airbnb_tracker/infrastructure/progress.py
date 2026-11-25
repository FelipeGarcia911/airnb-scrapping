"""Progress listeners: human-readable lines for the terminal and a JSON state file the
web UI polls (written atomically, so the UI never reads half a file)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from airbnb_tracker.application.progress import ProgressEvent, ProgressListener

PROGRESS_FILE_ENV_VAR = "AIRBNB_TRACKER_PROGRESS_FILE"


class ConsoleProgress:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __call__(self, event: ProgressEvent) -> None:
        print(f"  [{event.done}/{event.total}] {event.message}", file=self._stream, flush=True)


class JsonFileProgress:
    """Keeps the latest state of every phase, plus when each phase started."""

    def __init__(self, path: Path, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._path = path
        self._now = now
        self._phases: dict[str, dict[str, object]] = {}

    def __call__(self, event: ProgressEvent) -> None:
        now = self._now().isoformat()
        previous = self._phases.get(event.phase.value, {})
        self._phases[event.phase.value] = {
            "done": event.done,
            "total": event.total,
            "message": event.message,
            "started_at": previous.get("started_at", now),
            "updated_at": now,
        }
        state = {"current": event.phase.value, "phases": self._phases}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self._path)


class CompositeProgress:
    def __init__(self, *listeners: ProgressListener) -> None:
        self._listeners = listeners

    def __call__(self, event: ProgressEvent) -> None:
        for listener in self._listeners:
            listener(event)


def progress_for_cli(stream: TextIO) -> ProgressListener:
    """Console output, plus the JSON state file when the web UI asked for one."""
    path = os.environ.get(PROGRESS_FILE_ENV_VAR)
    console = ConsoleProgress(stream)
    return CompositeProgress(console, JsonFileProgress(Path(path))) if path else console
