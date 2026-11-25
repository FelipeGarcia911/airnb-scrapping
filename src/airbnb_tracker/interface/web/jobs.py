"""Background jobs: long runs execute the CLI in a separate process.

Running out of process keeps the page responsive, survives page reloads and reuses the
exact same code path as the command line. Output goes to a log file the UI tails.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")


class JobAlreadyRunningError(RuntimeError):
    pass


@dataclass
class Job:
    label: str
    args: tuple[str, ...]
    log_path: Path
    process: subprocess.Popen[bytes]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def succeeded(self) -> bool:
        return self.process.poll() == 0

    def log(self, max_lines: int = 200) -> str:
        if not self.log_path.exists():
            return ""
        lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])

    def progress(self) -> tuple[int, int] | None:
        """Latest ``[done/total]`` marker printed by the CLI, if any."""
        matches = _PROGRESS.findall(self.log())
        if not matches:
            return None
        done, total = matches[-1]
        return int(done), int(total)

    def elapsed_seconds(self) -> float:
        return (datetime.now(UTC) - self.started_at).total_seconds()


class JobRunner:
    """Runs one CLI job at a time (Airbnb requests must not run in parallel)."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self.current: Job | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.running

    def start(self, label: str, config: Path, args: Sequence[str]) -> Job:
        if self.busy:
            raise JobAlreadyRunningError("Another job is still running")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        log_path = self._log_dir / f"{stamp}_{args[0]}.log"
        command = [sys.executable, "-u", "-m", "airbnb_tracker", "-c", str(config), *args]
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)
        self.current = Job(label, tuple(args), log_path, process)
        return self.current

    def stop(self) -> None:
        if self.current and self.current.running:
            self.current.process.terminate()
