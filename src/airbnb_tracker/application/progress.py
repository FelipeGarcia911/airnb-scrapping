"""Progress reporting (Observer): use cases announce what they are doing, listeners
decide how to show it (console lines, a JSON file for the web UI, nothing at all)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Phase(StrEnum):
    PRICES = "prices"
    DETAILS = "details"
    FILES = "files"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    phase: Phase
    done: int  # items finished so far in this phase
    total: int
    message: str  # what is happening right now, in plain words


class ProgressListener(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...


def silent(event: ProgressEvent) -> None:
    """Null Object listener: use cases can always notify without checking for None."""
    del event
