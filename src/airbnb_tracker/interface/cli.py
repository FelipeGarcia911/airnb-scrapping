"""Command-line interface.

airbnb-tracker scan              # prices: every stay length x check-in date
airbnb-tracker enrich            # listing pages (weekly) and calendars (every 2 days)
airbnb-tracker report            # percentiles per stay length, availability
airbnb-tracker export -f csv     # listings, pricing_snapshots, calendar_snapshots
airbnb-tracker run               # scan + enrich + report + export
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import warnings
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from airbnb_tracker.application.exceptions import ApplicationError
from airbnb_tracker.application.progress import Phase, ProgressEvent
from airbnb_tracker.application.use_cases import (
    BuildReportQuery,
    EnrichCommand,
    EnrichPart,
    ExportQuery,
    ScanMarketCommand,
    latest_run_stays,
)
from airbnb_tracker.bootstrap import Container
from airbnb_tracker.domain.exceptions import DomainError
from airbnb_tracker.domain.value_objects import Money, StayPeriod
from airbnb_tracker.infrastructure.config import ConfigurationError, load_settings
from airbnb_tracker.infrastructure.exporters import UnknownFormatError
from airbnb_tracker.infrastructure.progress import progress_for_cli

CONFIG_ENV_VAR = "AIRBNB_TRACKER_CONFIG"
EXPORT_FORMATS = ("csv", "json")


class TrackerCli:
    """Thin controller: translates commands into use-case calls and prints results."""

    def __init__(self, container: Container, out: TextIO) -> None:
        self._c = container
        self._out = out
        self._progress = progress_for_cli(out)
        self._stays: list[StayPeriod] = container.settings.stays.resolve(
            container.clock.now().date()
        )

    def scan(self, _args: argparse.Namespace) -> None:
        search = self._c.settings.search
        self._print(f"Searching {len(self._stays)} check-in / stay-length combinations…")
        self._c.scan_market.execute(
            ScanMarketCommand(
                stays=self._stays,
                currency=self._c.settings.scraping.currency,
                filters=search.filters,
                center=search.center,
                radius_km=search.radius_km,
                template_url=search.url,
                complete_coverage=search.complete_coverage,
                max_listings=search.max_listings,
                competitor_filter=self._c.competitor_filter(),
            ),
            progress=self._progress,
        )

    def enrich(self, args: argparse.Namespace) -> None:
        settings = self._c.settings
        my_id = settings.my_listing.listing_id
        parts = frozenset(EnrichPart(p) for p in args.only) if args.only else frozenset(EnrichPart)
        max_listings = args.max if args.max is not None else settings.enrich.max_listings_per_run
        top = args.top if args.top is not None else settings.enrich.competitors
        scope = f"{top} nearest competitors" if top is not None else "all competitors"
        self._print(
            f"Enriching {scope}, up to {max_listings} this run ({', '.join(sorted(parts))})…"
        )
        summary = self._c.enrich_listings.execute(
            EnrichCommand(
                stays=self._collected_stays(),
                priority_ids=[my_id] if my_id else [],
                center=settings.search.center,
                parts=parts,
                competitors=top,
                max_listings=max_listings,
                details_refresh=settings.enrich.details_refresh,
                calendar_refresh=settings.enrich.calendar_refresh,
                reviews_refresh=settings.enrich.reviews_refresh,
            ),
            progress=self._progress,
        )
        fetched = " · ".join(f"{part.value}: {n}" for part, n in summary.fetched.items())
        self._print(
            f"Fetched {fetched} · up to date: {summary.up_to_date} · "
            f"pending for next run: {summary.pending}"
        )
        if summary.failed:
            self._print(f"  Failed: {', '.join(map(str, summary.failed))}")

    def report(self, args: argparse.Namespace) -> None:
        settings = self._c.settings
        price = args.my_price if args.my_price is not None else settings.my_listing.nightly_price
        bundle = self._c.build_report.execute(
            BuildReportQuery(
                stays=self._collected_stays(),
                my_listing_id=settings.my_listing.listing_id,
                my_nightly_price=Money(price, settings.scraping.currency) if price else None,
                availability_window_days=settings.enrich.availability_window_days,
            )
        )
        self._c.report_exporter().export(bundle)

    def export(self, args: argparse.Namespace) -> None:
        settings = self._c.settings
        self._progress(ProgressEvent(Phase.FILES, 0, 1, "Writing export files"))
        destination = self._c.export_dataset.execute(
            ExportQuery(
                as_of=self._c.clock.now(),
                my_listing_id=settings.my_listing.listing_id,
                my_location=settings.search.center,
                min_nights_window_days=settings.enrich.availability_window_days,
            ),
            self._c.dataset_exporter(args.format),
        )
        self._progress(ProgressEvent(Phase.FILES, 1, 1, f"Files written to {destination}/"))

    def run(self, args: argparse.Namespace) -> None:
        self.scan(args)
        self.enrich(args)
        self.report(args)
        self.export(args)

    def _collected_stays(self) -> list[StayPeriod]:
        """Stays of the latest search: reports must follow what was actually collected."""
        return latest_run_stays(self._c.repository) or self._stays

    def _print(self, text: str) -> None:
        print(text, file=self._out, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airbnb-tracker", description="Analyse your Airbnb competitors' prices."
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path(os.environ.get(CONFIG_ENV_VAR, "config.yaml")),
        help="Path to the configuration file (default: config.yaml)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed logs")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("scan", help="Search listings and store their prices")
    enrich = commands.add_parser("enrich", help="Fetch listing pages, calendars and reviews")
    report = commands.add_parser("report", help="Print the market analysis")
    export = commands.add_parser("export", help="Export listings, pricing and calendar snapshots")
    run = commands.add_parser("run", help="scan + enrich + report + export")
    commands.add_parser("ui", help="Open the web interface (Streamlit)")
    for sub in (report, run):
        sub.add_argument("--my-price", type=Decimal, help="Your nightly price (overrides config)")
    for sub in (export, run):
        sub.add_argument("-f", "--format", choices=EXPORT_FORMATS, default="csv")
    for sub in (enrich, run):
        sub.add_argument(
            "--only",
            action="append",
            choices=[part.value for part in EnrichPart],
            help="Fetch only this kind of data (repeatable). Default: all",
        )
        sub.add_argument(
            "--top", type=int, help="Only the N nearest competitors (overrides config)"
        )
        sub.add_argument("--max", type=int, help="Listings to visit in this run (overrides config)")
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        warnings.filterwarnings("ignore", category=UserWarning)
    if args.command == "ui":
        return launch_ui(args.config)
    try:
        cli = TrackerCli(Container(load_settings(args.config), stream=out), out)
        handlers: dict[str, Callable[[argparse.Namespace], None]] = {
            "scan": cli.scan,
            "enrich": cli.enrich,
            "report": cli.report,
            "export": cli.export,
            "run": cli.run,
        }
        handlers[args.command](args)
    except (ConfigurationError, UnknownFormatError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (ApplicationError, DomainError) as error:
        print(f"Execution error: {error}", file=sys.stderr)
        return 2
    return 0


def launch_ui(config: Path) -> int:
    """Start the Streamlit app; it does not need a valid config to open."""
    app = Path(__file__).parent / "web" / "app.py"
    from airbnb_tracker.interface.web.theme import streamlit_flags  # noqa: PLC0415 - UI-only import

    command = [sys.executable, "-m", "streamlit", "run", str(app), *streamlit_flags(),
               "--", "--config", str(config.resolve())]  # fmt: skip
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())
