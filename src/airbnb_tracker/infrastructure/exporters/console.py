from __future__ import annotations

import sys
from typing import TextIO

from airbnb_tracker.application.dto import DurationReport, ReportBundle
from airbnb_tracker.domain.market_analysis import PricePosition
from airbnb_tracker.domain.value_objects import Money

POSITION_LABELS = {
    PricePosition.BELOW_MARKET: "below market",
    PricePosition.COMPETITIVE: "competitive",
    PricePosition.PREMIUM: "premium",
}
_RULE = "=" * 96


class ConsoleReportExporter:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def export(self, bundle: ReportBundle) -> str:
        captured = bundle.data_captured_at
        lines = [
            _RULE,
            "Competitor nightly price by stay length"
            + (f"  ·  data from {captured:%Y-%m-%d %H:%M} UTC" if captured else ""),
            _RULE,
        ]
        if not bundle.durations:
            lines.append("No pricing data yet: run `scan` first.")
        else:
            lines += [_HEADER, *(_duration_line(d) for d in bundle.durations)]
            lines += _my_position(bundle)
        lines += ["", *_availability(bundle)]
        self._stream.write("\n".join(lines) + "\n")
        return "console"


_HEADER = (
    f"{'Nights':>6} {'Dates':>6} {'Listings':>8} {'P25':>13} {'Median':>13} {'P75':>13}"
    f" {'Median disc.':>12} {'Discounted':>10}"
)


def _duration_line(report: DurationReport) -> str:
    dist = report.distribution
    discount = f"{report.median_discount_pct:.0f}%" if report.median_discount_pct else "—"
    share = (
        f"{report.discounted_share_pct:.0f}%" if report.discounted_share_pct is not None else "—"
    )
    return (
        f"{report.nights:>6} {report.check_ins:>6} {report.listings:>8}"
        f" {_m(dist.p25 if dist else None):>13} {_m(dist.median if dist else None):>13}"
        f" {_m(dist.p75 if dist else None):>13} {discount:>12} {share:>10}"
    )


def _my_position(bundle: ReportBundle) -> list[str]:
    lines: list[str] = []
    for d in bundle.durations:
        if d.position is None or d.percentile is None:
            continue
        if not lines:
            lines = ["", f"Your nightly price ({_m(d.my_nightly)}) vs. the market:"]
        lines.append(
            f"  {d.nights:>3} nights: {POSITION_LABELS[d.position]:<13} "
            f"(more expensive than {d.percentile:.0f}%)"
        )
    return lines


def _availability(bundle: ReportBundle) -> list[str]:
    availability = bundle.availability
    if not availability.listings_measured:
        return ["No calendars yet: run `enrich` to measure availability and minimum stay."]
    lines = [
        f"Calendars of {availability.listings_measured} competitors "
        f"(next {bundle.availability_window_days} days):",
        f"  Unavailable days (approx. occupancy): "
        f"median {availability.median_unavailable_pct:.0f}%",
    ]
    if availability.median_min_nights is not None:
        lines.append(f"  Minimum stay: median {availability.median_min_nights:g} nights")
    if bundle.my_unavailable_pct is not None:
        lines.append(
            f"  Your listing: {bundle.my_unavailable_pct:.0f}% unavailable, "
            f"minimum stay {bundle.my_min_nights or '—'} nights"
        )
    lines.append("  Note: unavailable = booking, host block or stay restriction.")
    return lines


def _m(money: Money | None) -> str:
    if money is None:
        return "—"
    return f"{money.amount:,.0f} {money.currency}"
