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
import sys
import warnings
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from airbnb_tracker.application.exceptions import ApplicationError
from airbnb_tracker.application.use_cases import (
    BuildReportQuery,
    EnrichCommand,
    ExportQuery,
    ScanMarketCommand,
)
from airbnb_tracker.bootstrap import Container
from airbnb_tracker.domain.exceptions import DomainError
from airbnb_tracker.domain.value_objects import Money, StayPeriod
from airbnb_tracker.infrastructure.config import ConfigurationError, load_settings
from airbnb_tracker.infrastructure.exporters import UnknownFormatError

CONFIG_ENV_VAR = "AIRBNB_TRACKER_CONFIG"
EXPORT_FORMATS = ("csv", "json")


class TrackerCli:
    """Thin controller: translates commands into use-case calls and prints results."""

    def __init__(self, container: Container, out: TextIO) -> None:
        self._c = container
        self._out = out
        self._stays: list[StayPeriod] = container.settings.stays.resolve(
            container.clock.now().date()
        )

    def scan(self, _args: argparse.Namespace) -> None:
        search = self._c.settings.search
        self._print(f"Searching {len(self._stays)} check-in / stay-length combinations…")
        summaries = self._c.scan_market.execute(
            ScanMarketCommand(
                stays=self._stays,
                currency=self._c.settings.scraping.currency,
                filters=search.filters,
                center=search.center,
                radius_km=search.radius_km,
                template_url=search.url,
                competitor_filter=self._c.competitor_filter(),
            )
        )
        for s in summaries:
            detail = f"ERROR: {s.error}" if s.error else f"{s.kept} listings (of {s.found})"
            self._print(f"  {s.stay}: {detail}")

    def enrich(self, _args: argparse.Namespace) -> None:
        settings = self._c.settings
        my_id = settings.my_listing.listing_id
        summary = self._c.enrich_listings.execute(
            EnrichCommand(
                stays=self._stays,
                priority_ids=[my_id] if my_id else [],
                max_listings=settings.enrich.max_listings_per_run,
                details_refresh=settings.enrich.details_refresh,
                calendar_refresh=settings.enrich.calendar_refresh,
            )
        )
        self._print(
            f"Listing pages: {summary.details_fetched} · calendars: {summary.calendars_fetched} · "
            f"up to date: {summary.up_to_date} · pending for next run: {summary.pending}"
        )
        if summary.failed:
            self._print(f"  Failed: {', '.join(map(str, summary.failed))}")

    def report(self, args: argparse.Namespace) -> None:
        settings = self._c.settings
        price = args.my_price if args.my_price is not None else settings.my_listing.nightly_price
        bundle = self._c.build_report.execute(
            BuildReportQuery(
                stays=self._stays,
                my_listing_id=settings.my_listing.listing_id,
                my_nightly_price=Money(price, settings.scraping.currency) if price else None,
                availability_window_days=settings.enrich.availability_window_days,
            )
        )
        self._c.report_exporter().export(bundle)

    def export(self, args: argparse.Namespace) -> None:
        settings = self._c.settings
        destination = self._c.export_dataset.execute(
            ExportQuery(
                my_listing_id=settings.my_listing.listing_id,
                my_location=settings.search.center,
                min_nights_window_days=settings.enrich.availability_window_days,
            ),
            self._c.dataset_exporter(args.format),
        )
        self._print(f"Data exported to {destination}/")

    def run(self, args: argparse.Namespace) -> None:
        self.scan(args)
        self.enrich(args)
        self.report(args)
        self.export(args)

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
    commands.add_parser("enrich", help="Fetch listing pages and calendars")
    report = commands.add_parser("report", help="Print the market analysis")
    export = commands.add_parser("export", help="Export listings, pricing and calendar snapshots")
    run = commands.add_parser("run", help="scan + enrich + report + export")
    for sub in (report, run):
        sub.add_argument("--my-price", type=Decimal, help="Your nightly price (overrides config)")
    for sub in (export, run):
        sub.add_argument("-f", "--format", choices=EXPORT_FORMATS, default="csv")
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        warnings.filterwarnings("ignore", category=UserWarning)
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


if __name__ == "__main__":
    sys.exit(main())
