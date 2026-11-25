"""Background jobs: data updates run the CLI in a separate process.

Running out of process keeps the page responsive, survives page reloads and reuses the
exact code path of the command line. The CLI reports progress to a JSON file (see
``infrastructure/progress.py``) that the UI reads to show steps, counts and time left.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from airbnb_tracker.application.progress import Phase
from airbnb_tracker.infrastructure.progress import PROGRESS_FILE_ENV_VAR

PHASE_LABELS = {
    Phase.PRICES: "Prices",
    Phase.DETAILS: "Listing details",
    Phase.FILES: "Export files",
}


class JobAlreadyRunningError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PhaseProgress:
    done: int
    total: int
    message: str
    started_at: datetime
    updated_at: datetime

    @property
    def finished(self) -> bool:
        return self.total > 0 and self.done >= self.total

    def seconds_left(self, now: datetime) -> float | None:
        """Extrapolates the average time per item; unknown until one item is done."""
        if self.done == 0:
            return None
        per_item = (now - self.started_at).total_seconds() / self.done
        return per_item * max(self.total - self.done, 0)


@dataclass
class Job:
    label: str
    phases: tuple[Phase, ...]
    estimated_seconds: dict[Phase, float]
    log_path: Path
    progress_path: Path
    process: subprocess.Popen[bytes]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def succeeded(self) -> bool:
        return self.process.poll() == 0

    def log(self, max_lines: int = 300) -> str:
        if not self.log_path.exists():
            return ""
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])

    def progress(self) -> dict[Phase, PhaseProgress]:
        try:
            state = json.loads(self.progress_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {
            Phase(name): PhaseProgress(
                done=int(data["done"]),
                total=int(data["total"]),
                message=str(data["message"]),
                started_at=datetime.fromisoformat(data["started_at"]),
                updated_at=datetime.fromisoformat(data["updated_at"]),
            )
            for name, data in state.get("phases", {}).items()
        }

    def current_phase(self) -> Phase:
        progress = self.progress()
        unfinished = [p for p in self.phases if not (p in progress and progress[p].finished)]
        return unfinished[0] if unfinished else self.phases[-1]

    def elapsed_seconds(self) -> float:
        return (datetime.now(UTC) - self.started_at).total_seconds()

    def seconds_left(self) -> float | None:
        """Measured pace for the current step plus estimates for the steps after it."""
        now = datetime.now(UTC)
        progress = self.progress()
        current = self.current_phase()
        remaining = 0.0
        after_current = False
        for phase in self.phases:
            if phase == current:
                state = progress.get(phase)
                measured = state.seconds_left(now) if state else None
                if measured is None:
                    measured = self.estimated_seconds.get(phase)
                if measured is None:
                    return None
                remaining += measured
                after_current = True
            elif after_current:
                remaining += self.estimated_seconds.get(phase, 0.0)
        return remaining


class JobRunner:
    """Runs one update at a time (Airbnb requests must not run in parallel)."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self.current: Job | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.running

    def start(
        self,
        label: str,
        config: Path,
        args: Sequence[str],
        phases: Sequence[Phase],
        estimated_seconds: dict[Phase, float],
    ) -> Job:
        if self.busy:
            raise JobAlreadyRunningError("Another update is still running")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        log_path = self._log_dir / f"{stamp}_{args[0]}.log"
        progress_path = self._log_dir / f"{stamp}_{args[0]}.progress.json"
        command = [sys.executable, "-u", "-m", "airbnb_tracker", "-c", str(config), *args]
        env = {**os.environ, PROGRESS_FILE_ENV_VAR: str(progress_path)}
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT, env=env)
        self.current = Job(
            label, tuple(phases), estimated_seconds, log_path, progress_path, process
        )
        return self.current

    def stop(self) -> None:
        if self.current and self.current.running:
            self.current.process.terminate()

    def past_logs(self, limit: int = 10) -> list[Path]:
        if not self._log_dir.exists():
            return []
        return sorted(self._log_dir.glob("*.log"), reverse=True)[:limit]
