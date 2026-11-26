# airbnb-tracker

A command-line tool to **analyse the competition of an Airbnb listing**. It collects the
prices, availability, minimum stays and amenities of the listings competing with yours,
keeps the full history and exports it to CSV, JSON or SQLite for analysis.

It answers questions such as:

- How much do competitors charge per night for 7, 30 or 90-night stays?
- What monthly discount do they offer?
- How booked are their calendars and what minimum stay do they require?
- Where does my price sit in the market (percentile)?
- How much more do listings with a pool, air conditioning or parking charge?
- How active are competitors (recent reviews as a proxy for completed stays)?

> Built for analysing **your own market** (tens or hundreds of listings), not for mass
> scraping. It pauses between requests and retries with exponential backoff.

---

## How it works

```
 config.yaml ──► scan ────► pricing_snapshots  (price per listing × date × stay length)
  location,       │
  filters,        └─► listings found
  stay lengths          │
                        ▼
                     enrich ──► listings            (amenities, description, capacity…)
                                calendar_snapshots  (availability and minimum stay per day)
                                     │
                     report ◄────────┤  percentiles per stay length, discounts, availability
                     export ◄────────┘  listings / pricing_snapshots / calendar_snapshots
```

1. **`scan`** searches Airbnb with your location and filters for **every combination of
   stay length** (7, 14, 20, 28, 30, 60, 90 nights) **and check-in date** (one every 30 days
   over the next 6 months). It stores each listing's price and its position in the results.
2. **`enrich`** visits the page, calendar and reviews of your listing and of the N nearest
   competitors (`enrich.competitors`, 50 by default). It is incremental: listing pages and
   reviews are refreshed every 7 days, calendars every 2 days, and each run processes at most
   `max_listings_per_run` listings; the rest wait for the next run.
3. **`report`** prints a summary to the console.
4. **`export`** writes the three analysis files.

Nothing is overwritten: every run appends new observations, building a price and
availability history over time.

---

## Requirements

- Python **3.11+** (tested with 3.12)
- [uv](https://docs.astral.sh/uv/) to manage the environment: `brew install uv`

## Installation

```bash
git clone https://github.com/FelipeGarcia911/airnb-scrapping.git
cd airnb-scrapping
uv sync                              # creates .venv and installs dependencies
cp config.example.yaml config.yaml   # your local configuration (git-ignored)
```

---

## Configuration (`config.yaml`)

```yaml
my_listing:
  id: "123456789"          # your listing: airbnb.com/rooms/<ID> (exported with is_mine = true)
  nightly_price: 180000    # your nightly price, to position it against the market

search:
  latitude: 4.8133         # search center
  longitude: -75.6961
  radius_km: 6
  url: null                # alternative: an airbnb.com search URL with your filters
  complete_coverage: true  # splits the area so no listings are missed (~15% more results)
  max_listings: 50         # keep only the 50 nearest listings per search (null = all)
  guests: 2
  room_type: entire_place  # entire_place | private_room | null
  min_bedrooms: 2
  max_bedrooms: 3          # Airbnb has no such filter: applied after searching
  min_bathrooms: 1

stays:
  durations: [7, 14, 20, 28, 30, 60, 90]
  first_check_in_days: 7   # first check-in: today + 7 days
  every_days: 30           # one check-in date every 30 days...
  horizon_days: 180        # ...over 6 months

enrich:
  competitors: 50          # enrich only the 50 nearest competitors (null = all found)
  max_listings_per_run: 51
  details_refresh_days: 7
  calendar_refresh_days: 2
  reviews_refresh_days: 7
  availability_window_days: 60

scraping:
  currency: COP
  min_delay_seconds: 3     # pause between requests
```

`config.example.yaml` documents every option.

**Two ways to define the search:**

- **Center + radius** (recommended): `latitude`, `longitude` and `radius_km`. Listings outside
  the circle are discarded.
- **Airbnb URL**: run the search on airbnb.com with every filter you want, copy the URL and
  paste it into `search.url`. The tool replaces the dates for each combination.

To use a proxy, set `scraping.proxy_url` or the `AIRBNB_TRACKER_PROXY_URL` environment
variable (keeps credentials out of the file).

---

## Usage

```bash
uv run airbnb-tracker scan              # collect prices
uv run airbnb-tracker enrich            # listing pages and calendars
uv run airbnb-tracker report            # console summary
uv run airbnb-tracker export -f csv     # or -f json
uv run airbnb-tracker run               # all four steps
```

Useful options:

| Option | Description |
|---|---|
| `-c path/config.yaml` | Use another configuration file (also `AIRBNB_TRACKER_CONFIG`). |
| `report --my-price 175000` | Try another price of your own without editing the config. |
| `enrich --top 20` | Enrich only the 20 nearest competitors. |
| `enrich --only calendars` | Fetch only one kind of data: `details`, `calendars` or `reviews` (repeatable). |
| `enrich --max 100` | Visit up to 100 listings in this run. |
| `-v` | Show detailed logs (failed requests, retries). |

### Web interface

```bash
uv run airbnb-tracker ui        # opens http://localhost:8501
```

A local web app (Streamlit) organised as four steps, like the comp-set dashboards of
market tools: define your comp set, collect, analyse, export.

| Step | Page | What it is for |
|---|---|---|
| **1 · Set up** | Set up my market | Your listing (centre the search on it from its link), area, comparable filters, stay lengths and comp set size, with the resulting searches and run time. *Save and continue* takes you to step 2. |
| **2 · Collect** | Collect data | Starts the update (prices → listing details → export files) and follows it live: current step, what is being read, items done, elapsed time and time left. Partial updates and the log are here too. |
| **3 · Results** | Overview | Key numbers of your comp set and plain-language takeaways. |
| | Pricing advisor | A suggested (re)opening rate: a percentile of your closest comparables (same bedrooms, bathrooms, distance), nudged by your rating, reviews and amenities, with a rate per check-in date. Every factor is shown. |
| | Prices | Price ranges per stay length, length-of-stay discount curve and each listing's ladder, discounts, your position and seasonality by check-in date. |
| | Demand | Upcoming demand calendar (share of the comp set not bookable, day by day) with the busiest periods, and historical seasonality from review dates. |
| | Comp set | Map and list of competitors, and what price-moving amenities are worth (median price with vs without, same bedrooms). |
| | Positioning | Value map: price versus rating with quadrants (premium, overpriced, good value, budget) and where you fit. |
| | Availability | Nights not bookable in the next 30/60/90 days, minimum stays, and short-stay vs monthly competitors compared. |
| **4 · Export** | Export | Download every table as CSV or write them all to the exports folder. |

Every dashboard follows the same pattern: what it shows, the chart or table, *how to read
it*, a plain-language takeaway computed from your data, and its caveats. Results always use
the stays of the latest data collection, so they do not change when the day changes.

The app opens on step 1 until there is data, and on the results afterwards. While an update
runs you can move between steps: the sidebar keeps showing its progress. The CLI prints the
same progress, so a terminal run is never silent either.

The app only listens on `localhost`. Saving from the UI rewrites `config.yaml` without
comments; `config.example.yaml` keeps the documented version.

### Sample `report`

```
Nights  Dates Listings           P25        Median           P75 Median disc. Discounted
     7      2      310   142,036 COP   185,771 COP   270,241 COP          10%        64%
    30      2      344   116,194 COP   151,020 COP   208,916 COP          22%       100%
    60      2      332   115,581 COP   149,117 COP   217,305 COP          22%       100%

Your nightly price (180,000 COP) vs. the market:
    7 nights: competitive   (more expensive than 48%)
   30 nights: competitive   (more expensive than 64%)

Calendars of 120 competitors (next 60 days):
  Unavailable days (approx. occupancy): median 38%
  Minimum stay: median 2 nights
```

- **P25 / Median / P75**: nightly price of 25%, 50% and 75% of competitors. The P25–P75 band
  is the "competitive" range.
- **Median disc. / Discounted**: typical discount and share of listings offering one.

### Approximate run times

| Step | Full configuration |
|---|---|
| `scan` | 42 searches (7 stay lengths × 6 dates): roughly 5–10 min |
| `enrich` | 3 requests per listing (page, calendar, reviews): ~10 min for 50 listings |

To go faster, reduce `stays.durations`, set `every_days: 90` (2 check-in dates), use a smaller
`radius_km`, or run `enrich --only calendars` when you only need availability and minimum stay.
Interrupting with Ctrl+C is safe: every listing is saved as soon as it is fetched.

The first time, `enrich` needs several runs to cover every listing; afterwards it only
refreshes what has expired.

---

## Exported data

`export` writes five files (CSV or JSON) to `exports/` with the **full history**. Only your
listing and the N nearest competitors are enriched; filter `listings` on `details_collected_at`
to keep just those.

### `listings` — one row per listing

| Field | Description |
|---|---|
| `listing_id`, `url`, `title`, `description` | Identification and listing text |
| `is_mine` | `true` for your own listing |
| `latitude`, `longitude`, `distance_km` | Location and distance to the search center |
| `property_type`, `room_type` | Property type (Rental unit, Loft…) and room type |
| `bedrooms`, `bathrooms`, `beds`, `max_guests` | Layout and capacity |
| `rating`, `review_count` | Reputation |
| `minimum_nights` | Most frequent minimum stay in the calendar |
| `is_superhost`, `is_guest_favorite` | Badges |
| `amenities` | Amenity list (pipe-separated ` \| ` in CSV) |
| `photos` | Photo URLs |
| `last_review_at`, `reviews_last_90_days` | Latest review and recent review count (activity proxy) |
| `details_collected_at` | When the listing page was read |

### `pricing_snapshots` — one row per listing × search

| Field | Description |
|---|---|
| `listing_id`, `collected_at` | Listing and observation time |
| `check_in`, `check_out`, `nights`, `guests`, `currency` | Scenario searched |
| `original_price` | Stay price before discounts |
| `total_price` | **Stay price after discounts** (see limitations) |
| `nightly_price` | `total_price / nights` |
| `discount_pct` | Discount applied (weekly, monthly, special offer) |
| `is_available` | Bookable for those dates |
| `search_position` | Position among the listings kept from that search. It matches Airbnb's ranking only with `complete_coverage: false`; with complete coverage the area is split into several searches. |

### `calendar_snapshots` — one row per listing × observed day

| Field | Description |
|---|---|
| `listing_id`, `collected_at` | Listing and observation time |
| `date` | Calendar day |
| `available` | Whether the day could be booked |
| `minimum_nights` | Minimum stay required for that day |

### `reviews` — one row per review

| Field | Description |
|---|---|
| `listing_id`, `review_id` | Listing and review identifiers |
| `created_at` | Review date |
| `rating`, `language` | Star rating and language |

Reviewer names, photos and review texts are deliberately **not** stored (personal data).

### `search_runs` — one row per search

Parameters (dates, guests, filters, center, radius or URL), `results_found`, `results_kept`,
`duration_seconds`, `truncated` (Airbnb hit its page cap, results may be incomplete) and
`error`. Useful to evaluate how well each query covers the market. The number of result pages
is not available: pyairbnb does not expose it.

### SQLite database

Everything is also stored in `data/airbnb.db`, with two ready-to-query views:

```bash
sqlite3 data/airbnb.db "
  SELECT nights, COUNT(*) AS n, ROUND(AVG(nightly_price)) AS avg_nightly
  FROM v_pricing GROUP BY nights ORDER BY nights;"
```

- `v_pricing`: prices with nights, nightly price and search position.
- `v_calendar`: day-by-day calendars.

Reviews and search runs live in the `reviews` and `search_runs` tables.

### Analysis example with pandas

```python
import pandas as pd

listings = pd.read_csv("exports/listings.csv")
pricing = pd.read_csv("exports/pricing_snapshots.csv")
df = pricing.merge(listings, on="listing_id")

# Nightly price percentiles per stay length
df.groupby("nights")["nightly_price"].quantile([0.25, 0.5, 0.75]).unstack()

# Price premium of an amenity (e.g. pool) for 30-night stays
month = df[df.nights == 30]
has_pool = month.amenities.fillna("").str.contains("Pool")
month.groupby(has_pool)["nightly_price"].median()
```

---

## Known limitations

- **`total_price` excludes cleaning fee, service fee and taxes.** Airbnb does not expose
  those charges in search results or in the listing price lookup (verified on several
  listings: only nights × rate and discounts are returned). It is the stay price with
  discounts applied.
- **Stays of 28+ nights** are shown by Airbnb as an *average monthly price*; the tool converts
  it to the stay total (60 nights = 2 months).
- **"Unavailable" is not "booked"**: it may be a booking, a host block or a stay restriction.
  Treat it as an approximation of occupancy.
- **Outliers**: some hosts set absurd prices (hundreds of millions) to block bookings. The
  report uses percentiles, which are unaffected, but filter them in your own analyses.
- **Airbnb changes its internal API often.** Extraction relies on
  [pyairbnb](https://pypi.org/project/pyairbnb/); if something breaks, upgrade it first
  (`uv lock --upgrade-package pyairbnb && uv sync`).
- Responses are requested in English because parsing ("for 7 nights", "2 bedrooms") depends
  on it.

> **Responsible use**: Airbnb's terms of service restrict automated extraction. Use this tool
> sparingly, for internal analysis, without collecting personal data of hosts or guests,
> and do not publish the scraped data.

---

## Scheduling collection

Recommended frequencies: prices and calendars every 2–3 days, listing pages weekly (the latter
is already handled by `details_refresh_days`). With `cron`, e.g. every 2 days at 06:00:

```cron
0 6 */2 * * cd /path/to/airnb-scrapping && { /opt/homebrew/bin/uv run airbnb-tracker scan && /opt/homebrew/bin/uv run airbnb-tracker enrich; } >> data/cron.log 2>&1
```

---

## Architecture

The project follows **Clean Architecture**: dependencies always point inwards, and the domain
knows nothing about Airbnb, SQLite or the console.

```
src/airbnb_tracker/
├── domain/            Pure business rules (no external dependencies)
│   ├── value_objects.py   Money, StayPeriod, GeoPoint, BoundingBox, ListingId
│   ├── entities.py        Listing, PriceQuote, ListingOffer, Snapshot
│   ├── profile.py         ListingProfile, Amenity, CalendarSnapshot, AvailabilitySummary
│   ├── specifications.py  Composable competitor filters (radius, bedrooms…)
│   ├── market_analysis.py Percentiles, price position, discounts
│   └── stay_planner.py    Check-in date × stay length combinations
├── application/       Use cases and ports (interfaces)
│   ├── ports.py           Gateways and repositories implemented by infrastructure
│   ├── dto.py             Input and output objects
│   └── use_cases/         scan_market, enrich_listings, build_market_report, export_dataset
├── infrastructure/    Replaceable details
│   ├── airbnb/            pyairbnb adapters and Airbnb payload translation
│   ├── persistence/       SQLite and in-memory repositories
│   ├── exporters/         Console, CSV and JSON
│   ├── resilience.py      Request throttling and retries
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

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # formatting
uv run mypy            # type checking (strict)
```
