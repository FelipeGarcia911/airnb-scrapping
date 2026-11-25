"""SQLite persistence. Append-only: every observation is kept, so the database doubles
as the analysis dataset (see the ``v_pricing`` and ``v_calendar`` views)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from airbnb_tracker.application.dto import SearchFilters, SearchRun
from airbnb_tracker.domain.entities import Listing, ListingOffer, PriceQuote, RoomType, Snapshot
from airbnb_tracker.domain.profile import (
    Amenity,
    CalendarDay,
    CalendarSnapshot,
    ListingProfile,
    Review,
)
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId, Money, StayPeriod

_LISTING_COLUMNS_DDL = """
    listing_id        TEXT    NOT NULL,
    name              TEXT    NOT NULL,
    room_type         TEXT    NOT NULL,
    property_type     TEXT    NOT NULL,
    latitude          REAL,
    longitude         REAL,
    bedrooms          INTEGER,
    beds              INTEGER,
    bathrooms         REAL,
    rating            REAL,
    review_count      INTEGER NOT NULL,
    is_guest_favorite INTEGER NOT NULL,
    photos            TEXT    NOT NULL"""

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT    NOT NULL,
    check_in    TEXT    NOT NULL,
    check_out   TEXT    NOT NULL,
    currency    TEXT    NOT NULL,
    guests      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_stay ON snapshots (check_in, check_out, captured_at);

CREATE TABLE IF NOT EXISTS offers (
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    search_position INTEGER NOT NULL,
    {_LISTING_COLUMNS_DDL},
    total           TEXT,
    original_total  TEXT,
    PRIMARY KEY (snapshot_id, listing_id)
);
CREATE INDEX IF NOT EXISTS ix_offers_listing ON offers (listing_id);

CREATE TABLE IF NOT EXISTS listing_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT    NOT NULL,
    {_LISTING_COLUMNS_DDL},
    description     TEXT    NOT NULL,
    person_capacity INTEGER,
    is_superhost    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_profiles_listing ON listing_profiles (listing_id, captured_at);

CREATE TABLE IF NOT EXISTS profile_amenities (
    profile_id INTEGER NOT NULL REFERENCES listing_profiles (id) ON DELETE CASCADE,
    code       TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    category   TEXT    NOT NULL,
    PRIMARY KEY (profile_id, code, name)
);

CREATE TABLE IF NOT EXISTS calendar_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_calendars_listing ON calendar_snapshots (listing_id, captured_at);

CREATE TABLE IF NOT EXISTS calendar_days (
    calendar_id INTEGER NOT NULL REFERENCES calendar_snapshots (id) ON DELETE CASCADE,
    day         TEXT    NOT NULL,
    available   INTEGER NOT NULL,
    min_nights  INTEGER,
    PRIMARY KEY (calendar_id, day)
);

CREATE VIEW IF NOT EXISTS v_pricing AS
SELECT o.listing_id, s.captured_at, s.check_in, s.check_out,
       CAST(julianday(s.check_out) - julianday(s.check_in) AS INTEGER) AS nights,
       s.guests, s.currency,
       CAST(COALESCE(o.original_total, o.total) AS REAL) AS original_price,
       CAST(o.total AS REAL) AS total_price,
       ROUND(CAST(o.total AS REAL) / (julianday(s.check_out) - julianday(s.check_in)), 2)
           AS nightly_price,
       o.total IS NOT NULL AS is_available,
       o.search_position
FROM offers o JOIN snapshots s ON s.id = o.snapshot_id;

CREATE TABLE IF NOT EXISTS reviews (
    review_id  TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    rating     INTEGER,
    language   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reviews_listing ON reviews (listing_id, created_at);

CREATE TABLE IF NOT EXISTS review_fetches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   TEXT    NOT NULL,
    fetched_at   TEXT    NOT NULL,
    review_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_fetches ON review_fetches (listing_id, fetched_at);

CREATE TABLE IF NOT EXISTS search_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL,
    started_at        TEXT    NOT NULL,
    finished_at       TEXT    NOT NULL,
    check_in          TEXT    NOT NULL,
    check_out         TEXT    NOT NULL,
    guests            INTEGER NOT NULL,
    currency          TEXT    NOT NULL,
    filters           TEXT    NOT NULL,
    latitude          REAL,
    longitude         REAL,
    radius_km         REAL,
    url               TEXT,
    complete_coverage INTEGER NOT NULL,
    results_found     INTEGER NOT NULL,
    results_kept      INTEGER NOT NULL,
    truncated         INTEGER NOT NULL,
    error             TEXT
);

CREATE VIEW IF NOT EXISTS v_calendar AS
SELECT c.listing_id, c.captured_at, d.day, d.available, d.min_nights
FROM calendar_days d JOIN calendar_snapshots c ON c.id = d.calendar_id;
"""

_LISTING_COLUMNS = (
    "listing_id", "name", "room_type", "property_type", "latitude", "longitude", "bedrooms",
    "beds", "bathrooms", "rating", "review_count", "is_guest_favorite", "photos",
)  # fmt: skip
_OFFER_COLUMNS = ("search_position", *_LISTING_COLUMNS, "total", "original_total")
_PROFILE_COLUMNS = ("captured_at", *_LISTING_COLUMNS, "description", "person_capacity",
                    "is_superhost")  # fmt: skip


class SqliteRepository:
    """Implements every repository port: snapshots, profiles, calendars, reviews, runs."""

    def __init__(self, database_path: Path | str) -> None:
        self._path = str(database_path)
        in_memory = self._path == ":memory:"
        if not in_memory:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._shared_connection = self._open() if in_memory else None
        with self._connection() as connection:
            connection.executescript(_SCHEMA)

    # --- pricing snapshots --------------------------------------------------------------
    def add(self, snapshot: Snapshot) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots (captured_at, check_in, check_out, currency, guests) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    snapshot.captured_at.isoformat(),
                    snapshot.stay.check_in.isoformat(),
                    snapshot.stay.check_out.isoformat(),
                    snapshot.currency,
                    snapshot.guests,
                ),
            )
            rows = [
                (cursor.lastrowid, position, *_listing_values(o.listing), *_quote_values(o))
                for position, o in enumerate(snapshot.offers, start=1)
            ]
            connection.executemany(_insert("offers", ("snapshot_id", *_OFFER_COLUMNS)), rows)

    def latest(self, stay: StayPeriod) -> Snapshot | None:
        with self._connection() as connection:
            header = connection.execute(
                "SELECT * FROM snapshots WHERE check_in = ? AND check_out = ? "
                "ORDER BY captured_at DESC, id DESC LIMIT 1",
                (stay.check_in.isoformat(), stay.check_out.isoformat()),
            ).fetchone()
            return self._load_snapshot(connection, header) if header else None

    def all_snapshots(self) -> list[Snapshot]:
        with self._connection() as connection:
            headers = connection.execute("SELECT * FROM snapshots ORDER BY id").fetchall()
            return [self._load_snapshot(connection, header) for header in headers]

    @staticmethod
    def _load_snapshot(connection: sqlite3.Connection, header: sqlite3.Row) -> Snapshot:
        stay = StayPeriod(
            date.fromisoformat(header["check_in"]), date.fromisoformat(header["check_out"])
        )
        captured_at = datetime.fromisoformat(header["captured_at"])
        rows = connection.execute(
            "SELECT * FROM offers WHERE snapshot_id = ? ORDER BY search_position", (header["id"],)
        ).fetchall()
        offers = tuple(_row_to_offer(r, stay, header["currency"], captured_at) for r in rows)
        return Snapshot(captured_at, stay, header["currency"], header["guests"], offers)

    # --- listing profiles ---------------------------------------------------------------
    def save_profile(self, profile: ListingProfile) -> None:
        values = (
            profile.captured_at.isoformat(),
            *_listing_values(profile.listing),
            profile.description,
            profile.person_capacity,
            int(profile.is_superhost),
        )
        with self._connection() as connection:
            cursor = connection.execute(_insert("listing_profiles", _PROFILE_COLUMNS), values)
            connection.executemany(
                _insert("profile_amenities", ("profile_id", "code", "name", "category")),
                [(cursor.lastrowid, a.code, a.name, a.category) for a in profile.amenities],
            )

    def latest_profiles(self) -> dict[ListingId, ListingProfile]:
        latest_ids = _latest_ids_sql("listing_profiles")
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM listing_profiles WHERE id IN ({latest_ids})"
            ).fetchall()
            amenities: dict[int, set[Amenity]] = {}
            for a in connection.execute(
                f"SELECT * FROM profile_amenities WHERE profile_id IN ({latest_ids})"
            ):
                amenities.setdefault(a["profile_id"], set()).add(
                    Amenity(a["code"], a["name"], a["category"])
                )
        return {
            ListingId(row["listing_id"]): ListingProfile(
                listing=_row_to_listing(row),
                captured_at=datetime.fromisoformat(row["captured_at"]),
                amenities=frozenset(amenities.get(row["id"], set())),
                description=row["description"],
                person_capacity=_opt_int(row["person_capacity"]),
                is_superhost=bool(row["is_superhost"]),
            )
            for row in rows
        }

    # --- calendar snapshots -------------------------------------------------------------
    def save_calendar(self, calendar: CalendarSnapshot) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO calendar_snapshots (listing_id, captured_at) VALUES (?, ?)",
                (str(calendar.listing_id), calendar.captured_at.isoformat()),
            )
            connection.executemany(
                _insert("calendar_days", ("calendar_id", "day", "available", "min_nights")),
                [
                    (cursor.lastrowid, d.day.isoformat(), int(d.available), d.min_nights)
                    for d in calendar.days
                ],
            )

    def latest_calendars(self) -> dict[ListingId, CalendarSnapshot]:
        calendars = self._load_calendars(_latest_ids_sql("calendar_snapshots"))
        return {calendar.listing_id: calendar for calendar in calendars}

    def all_calendars(self) -> list[CalendarSnapshot]:
        return self._load_calendars("SELECT id FROM calendar_snapshots")

    def _load_calendars(self, id_query: str) -> list[CalendarSnapshot]:
        with self._connection() as connection:
            headers = connection.execute(
                f"SELECT * FROM calendar_snapshots WHERE id IN ({id_query}) ORDER BY id"
            ).fetchall()
            days: dict[int, list[CalendarDay]] = {}
            for d in connection.execute(
                f"SELECT * FROM calendar_days WHERE calendar_id IN ({id_query}) ORDER BY day"
            ):
                days.setdefault(d["calendar_id"], []).append(
                    CalendarDay(
                        date.fromisoformat(d["day"]),
                        bool(d["available"]),
                        _opt_int(d["min_nights"]),
                    )
                )
        return [
            CalendarSnapshot(
                listing_id=ListingId(h["listing_id"]),
                captured_at=datetime.fromisoformat(h["captured_at"]),
                days=tuple(days.get(h["id"], [])),
            )
            for h in headers
        ]

    # --- reviews ------------------------------------------------------------------------
    def save_reviews(
        self, listing_id: ListingId, fetched_at: datetime, reviews: Sequence[Review]
    ) -> None:
        with self._connection() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO reviews (review_id, listing_id, created_at, rating, "
                "language) VALUES (?, ?, ?, ?, ?)",
                [
                    (r.review_id, str(r.listing_id), r.created_at.isoformat(), r.rating, r.language)
                    for r in reviews
                ],
            )
            connection.execute(
                _insert("review_fetches", ("listing_id", "fetched_at", "review_count")),
                (str(listing_id), fetched_at.isoformat(), len(reviews)),
            )

    def latest_review_fetches(self) -> dict[ListingId, datetime]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT listing_id, MAX(fetched_at) AS fetched_at FROM review_fetches "
                "GROUP BY listing_id"
            ).fetchall()
        return {ListingId(r["listing_id"]): datetime.fromisoformat(r["fetched_at"]) for r in rows}

    def all_reviews(self) -> list[Review]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM reviews ORDER BY listing_id, created_at"
            ).fetchall()
        return [
            Review(
                listing_id=ListingId(r["listing_id"]),
                review_id=r["review_id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                rating=_opt_int(r["rating"]),
                language=r["language"],
            )
            for r in rows
        ]

    # --- search runs --------------------------------------------------------------------
    def add_run(self, run: SearchRun) -> None:
        values = {
            "run_id": run.run_id,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat(),
            "check_in": run.stay.check_in.isoformat(),
            "check_out": run.stay.check_out.isoformat(),
            "guests": run.guests,
            "currency": run.currency,
            "filters": json.dumps(_filters_to_dict(run.filters)),
            "latitude": run.center.latitude if run.center else None,
            "longitude": run.center.longitude if run.center else None,
            "radius_km": run.radius_km,
            "url": run.url,
            "complete_coverage": int(run.complete_coverage),
            "results_found": run.results_found,
            "results_kept": run.results_kept,
            "truncated": int(run.truncated),
            "error": run.error,
        }
        with self._connection() as connection:
            connection.execute(_insert("search_runs", tuple(values)), tuple(values.values()))

    def all_runs(self) -> list[SearchRun]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM search_runs ORDER BY id").fetchall()
        return [_row_to_run(r) for r in rows]

    # --- connection handling ------------------------------------------------------------
    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._shared_connection is not None:
            with self._shared_connection:
                yield self._shared_connection
            return
        connection = self._open()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _insert(table: str, columns: Sequence[str]) -> str:
    placeholders = ", ".join("?" * len(columns))
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


def _latest_ids_sql(table: str) -> str:
    """Ids of the most recent row per listing."""
    return (
        f"SELECT id FROM {table} t WHERE id = (SELECT t2.id FROM {table} t2 "
        "WHERE t2.listing_id = t.listing_id ORDER BY t2.captured_at DESC, t2.id DESC LIMIT 1)"
    )


def _listing_values(listing: Listing) -> tuple[object, ...]:
    return (
        str(listing.id),
        listing.name,
        listing.room_type.value,
        listing.property_type,
        listing.location.latitude if listing.location else None,
        listing.location.longitude if listing.location else None,
        listing.bedrooms,
        listing.beds,
        listing.bathrooms,
        listing.rating,
        listing.review_count,
        int(listing.is_guest_favorite),
        json.dumps(list(listing.photos)),
    )


def _quote_values(offer: ListingOffer) -> tuple[object, ...]:
    quote = offer.quote
    return (
        str(quote.total.amount) if quote else None,
        str(quote.original_total.amount) if quote and quote.original_total else None,
    )


def _row_to_listing(row: sqlite3.Row) -> Listing:
    latitude, longitude = _opt_float(row["latitude"]), _opt_float(row["longitude"])
    return Listing(
        id=ListingId(row["listing_id"]),
        name=row["name"],
        room_type=RoomType(row["room_type"]),
        property_type=row["property_type"],
        location=GeoPoint(latitude, longitude) if latitude and longitude else None,
        bedrooms=_opt_int(row["bedrooms"]),
        beds=_opt_int(row["beds"]),
        bathrooms=_opt_float(row["bathrooms"]),
        rating=_opt_float(row["rating"]),
        review_count=int(row["review_count"]),
        is_guest_favorite=bool(row["is_guest_favorite"]),
        photos=tuple(json.loads(row["photos"])),
    )


def _row_to_offer(
    row: sqlite3.Row, stay: StayPeriod, currency: str, captured_at: datetime
) -> ListingOffer:
    listing = _row_to_listing(row)
    if row["total"] is None:
        return ListingOffer(listing)
    original = row["original_total"]
    quote = PriceQuote(
        listing_id=listing.id,
        stay=stay,
        total=Money(Decimal(row["total"]), currency),
        original_total=Money(Decimal(original), currency) if original else None,
        captured_at=captured_at,
    )
    return ListingOffer(listing, quote)


def _filters_to_dict(filters: SearchFilters) -> dict[str, object]:
    values = asdict(filters)
    values["room_type"] = filters.room_type.value if filters.room_type else None
    return values


def _row_to_run(row: sqlite3.Row) -> SearchRun:
    filters = json.loads(row["filters"])
    room_type = filters.pop("room_type", None)
    latitude, longitude = row["latitude"], row["longitude"]
    return SearchRun(
        run_id=row["run_id"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]),
        stay=StayPeriod(date.fromisoformat(row["check_in"]), date.fromisoformat(row["check_out"])),
        guests=row["guests"],
        currency=row["currency"],
        filters=SearchFilters(room_type=RoomType(room_type) if room_type else None, **filters),
        center=GeoPoint(latitude, longitude) if latitude is not None else None,
        radius_km=_opt_float(row["radius_km"]),
        url=row["url"],
        complete_coverage=bool(row["complete_coverage"]),
        results_found=row["results_found"],
        results_kept=row["results_kept"],
        truncated=bool(row["truncated"]),
        error=row["error"],
    )


def _opt_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _opt_float(value: object) -> float | None:
    return None if value is None else float(str(value))
