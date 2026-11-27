# Architecture

The project follows **Clean Architecture**: dependencies always point inwards, and the domain
knows nothing about Airbnb, SQLite or the console.

```
src/airbnb_tracker/
├── domain/            Pure business rules (no external dependencies)
│   ├── value_objects.py   Money, StayPeriod, GeoPoint, BoundingBox, ListingId
│   ├── entities.py        Listing, PriceQuote, ListingOffer, Snapshot
│   ├── price_reading.py   What a displayed price means: unit, normalisation, exclusion
│   ├── profile.py         ListingProfile, Amenity, CalendarSnapshot, AvailabilitySummary
│   ├── specifications.py  Composable competitor filters (radius, bedrooms…)
│   ├── market_analysis.py Percentiles, price position, discounts
│   └── stay_planner.py    Check-in date × stay length combinations
├── application/       Use cases and ports (interfaces)
│   ├── ports.py           Gateways and repositories implemented by infrastructure
│   ├── dto.py             Input/output objects, SearchConditions, search statuses
│   └── use_cases/         scan_market, enrich_listings, build_market_report, export_dataset
├── infrastructure/    Replaceable details
│   ├── airbnb/            pyairbnb adapters and Airbnb payload translation
│   ├── persistence/       SQLite and in-memory repositories
│   ├── exporters/         Console, CSV and JSON
│   ├── resilience.py      Request throttling and retries
│   ├── versions.py        Collector version recorded on every search
│   └── config.py          config.yaml loading and validation
├── interface/
│   ├── cli.py             Command-line interface
│   └── web/               Streamlit UI: pages/, sidebar status, background jobs
└── bootstrap.py       Composition root: wires everything together
```

**SOLID principles**

| Principle | Where |
|---|---|
| Single responsibility | Each use case does one thing; mappers only translate Airbnb's format; exporters only format. |
| Open/closed | New export formats or filters are added without modifying existing code. |
| Liskov substitution | `SqliteRepository` and `InMemoryRepository` honour the same contract. |
| Interface segregation | Separate ports for search, listing pages and calendars, and per repository. |
| Dependency inversion | Use cases depend on `Protocol`s; `bootstrap.py` injects the implementations. |

**Design patterns ([Refactoring Guru](https://refactoring.guru/design-patterns) catalogue)**

| Pattern | Usage |
|---|---|
| Adapter | `PyAirbnb*Gateway` adapts pyairbnb to the application ports. |
| Decorator | `Resilient*Gateway` adds throttling and retries to any gateway. |
| Strategy + Factory Method | `DatasetExporterFactory` picks the CSV or JSON exporter. |
| Composite / Specification | Competitor filters combine with `&` and `~`. |
| Null Object | `AnyListing` accepts everything without conditionals. |

Refactorings applied: *Replace Primitive with Object* (`Money`, `StayPeriod`), *Introduce
Parameter Object* (`SearchFilters`, `SearchCriteria`, `DisplayedPrice`) and an
anti-corruption layer that isolates Airbnb's payload shape in
`infrastructure/airbnb/mappers.py`.

---

## Data integrity rules

These rules hold for any listing, stay length and market. They protect every price
recommendation.

### 1 · What a price means (`domain/price_reading.py`)

- A captured price is a `PriceReading`: the amount as shown, its currency, the qualifier
  text, the unit read from it, how it was normalised and, if it was, why it was excluded.
- The qualifier is parsed in `infrastructure/airbnb/text_parsing.py`. Only explicit wording
  is trusted ("for N nights", "night", "weekly", "monthly"); anything else is `unknown`.
- Only comparable readings become a `PriceQuote`. The others are stored on the offer as
  `rejected` (the listing is available, the price is not usable) and are never part of a
  median, percentile or outlier check.
- Rows stored before units existed load as `not_recorded`, so they are excluded too.

### 2 · Which search a price comes from (`application/dto.py` → `SearchConditions`)

- Every search gets a `search_id`; its snapshot and `SearchRun` share it.
- `SearchConditions` holds everything that makes two captures comparable except the
  dates: source (area or URL), guests, currency, area or URL without dates, filters,
  listing limit and coverage mode. Its `key` (hash of the canonical JSON) is stored on
  snapshots and runs. `Container.search_conditions()` builds it from the configuration and
  is the only place that does.
- `SnapshotRepository.latest(stay, conditions_key)` returns the latest capture with those
  conditions from a search that did not fail. Report, enrichment and dashboards all go
  through the key, so changing guests or filters never mixes references.
- The collector version (`infrastructure/versions.py`) is recorded on every run.

### 3 · How a search ended (`classify_search`)

| Status | When |
|---|---|
| `complete` | Rows arrived and all were interpreted |
| `partial` | Airbnb truncated the results or some rows could not be interpreted |
| `empty` | Airbnb returned no rows (a legitimate result) |
| `failed` | `request_failed` (network, block), `format_changed` (rows but none readable) or `prices_unreadable` (listings but no readable price) |

Failed searches keep their rows for diagnosis but are skipped when picking the reference,
so the previous valid capture with the same conditions keeps serving. The price screen
shows, per date, the search status, the capture's `search_id` and time, and warns when a
newer attempt failed. *Data quality → Searches and price interpretation* lists the counts
and the excluded prices by reason.

### Storage

SQLite migrations only add columns (`_ADDED_COLUMNS` in `persistence/sqlite.py`); nothing
is rewritten or deleted. Views are recreated on start.

### Tests that guard these rules

`tests/unit/application/test_capture_integrity.py`: unit parsing and normalisation, exclusion
reasons, statuses, conditions key, and the end-to-end case: two searches on the same dates
with different guests, one failing, and the report reusing only the matching history.

---

## Web UI data flow (`interface/web/data.py`)

`MarketData` exposes three views of the same data:

- `raw_tables`: everything collected, every condition and status (downloads).
- `comp_set_tables`: current conditions, non-failed searches, comparable property kinds,
  outliers included (data quality).
- `tables`: the same without price outliers when they are excluded (every analysis).

Cached with `st.cache_data`, keyed on the database and config.yaml modification times.

## Settings and preferences

- `config.yaml` holds the search profile and the listing configuration (`my_listing`,
  `calculator`) plus app preferences under `ui` (language, regional format, time zone,
  start page, export format, theme, density). See `interface/web/preferences.py`.
- The last selection on the pricing screen lives in `data/ui_state.json`, so clicking
  around does not invalidate the data cache.
- Interface text is English in code, wrapped in `t()`; Spanish lives in
  `interface/web/locales/es/*.json` (one key per file). `t()` formats numbers and dates
  in its placeholders by region.
