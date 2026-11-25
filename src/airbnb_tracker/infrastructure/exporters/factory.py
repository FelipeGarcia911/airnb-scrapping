"""Factory for dataset exporters (Strategy selected by name).

Adding a format means registering one more builder; no caller changes (OCP).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from airbnb_tracker.application.ports import DatasetExporter
from airbnb_tracker.infrastructure.exporters.dataset import CsvDatasetExporter, JsonDatasetExporter


class UnknownFormatError(ValueError):
    pass


class DatasetExporterFactory:
    def __init__(self, output_dir: Path) -> None:
        self._builders: dict[str, Callable[[], DatasetExporter]] = {
            "csv": lambda: CsvDatasetExporter(output_dir),
            "json": lambda: JsonDatasetExporter(output_dir),
        }

    @property
    def formats(self) -> tuple[str, ...]:
        return tuple(self._builders)

    def register(self, name: str, builder: Callable[[], DatasetExporter]) -> None:
        self._builders[name] = builder

    def create(self, name: str) -> DatasetExporter:
        try:
            return self._builders[name]()
        except KeyError:
            raise UnknownFormatError(
                f"Unknown format {name!r}. Options: {', '.join(self.formats)}"
            ) from None
