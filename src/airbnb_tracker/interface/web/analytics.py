"""Market analytics on the exported tables (pure pandas, no Streamlit: easy to test).

Every function works on the same frames the CSV export produces, so what the analyst
sees in the app can always be reproduced from the downloaded files.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

MONTHLY_STAY_NIGHTS = 28
PERCENTILE_STEP = 8  # how far each pricing signal moves the target percentile
MIN_COMPARABLES = 5
CONFIDENT_COMPARABLES = 15  # below this, percentiles move a lot with one listing
MIN_GROUP_SIZE = 5
CONFIDENT_GROUP_SIZE = 10  # both sides at least this big: the difference is worth quoting


# --- shared selections -----------------------------------------------------------------


def competitors(listings: pd.DataFrame) -> pd.DataFrame:
    """Analysed listings that are not yours."""
    if listings.empty:
        return listings
    analysed = listings[listings["details_collected_at"].notna()]
    return analysed[~analysed["is_mine"]]


def my_listing(listings: pd.DataFrame) -> pd.Series | None:
    mine = listings[listings["is_mine"]] if not listings.empty else listings
    return mine.iloc[0] if len(mine) else None


def latest_pricing(pricing: pd.DataFrame) -> pd.DataFrame:
    """Rows of the most recent search for every (check-in, nights) scenario."""
    if pricing.empty:
        return pricing
    newest = pricing.groupby(["check_in", "nights"])["collected_at"].transform("max")
    return pricing[pricing["collected_at"] == newest]


def nightly_by_listing(pricing: pd.DataFrame, nights: int) -> pd.Series:
    """Median nightly price of each listing for one stay length, across check-in dates."""
    latest = latest_pricing(pricing)
    rows = latest[(latest["nights"] == nights) & latest["nightly_price"].notna()]
    return rows.groupby("listing_id")["nightly_price"].median()


# --- 2 · length-of-stay ladder ---------------------------------------------------------


def los_curve(pricing: pd.DataFrame, listing_ids: set[str] | None = None) -> pd.DataFrame:
    """Market nightly price (P25 / median / P75) for each stay length."""
    latest = latest_pricing(pricing)
    if listing_ids is not None:
        latest = latest[latest["listing_id"].isin(listing_ids)]
    per_listing = latest.groupby(["nights", "listing_id"])["nightly_price"].median()
    grouped = per_listing.groupby(level="nights")
    curve = pd.DataFrame(
        {
            "P25": grouped.quantile(0.25),
            "Median": grouped.median(),
            "P75": grouped.quantile(0.75),
            "Listings": grouped.size(),
        }
    )
    base = curve["Median"].iloc[0] if len(curve) else None
    curve["Change vs shortest"] = (curve["Median"] / base - 1) * 100 if base else None
    return curve.round(0)


def los_ladder(pricing: pd.DataFrame, listings: pd.DataFrame) -> pd.DataFrame:
    """Per listing: nightly price at each stay length and the drop from the shortest one."""
    latest = latest_pricing(pricing)
    table = latest.groupby(["listing_id", "nights"])["nightly_price"].median().unstack("nights")
    if table.empty:
        return table
    lengths = list(table.columns)
    shortest, longest = lengths[0], lengths[-1]
    table = table.dropna(subset=[shortest, longest])
    drop = (1 - table[longest] / table[shortest]) * 100
    names = listings.set_index("listing_id")[["title", "is_mine"]]
    out = table.round(0).rename(columns=lambda n: f"{n} nights")
    out[f"Drop {shortest}→{longest} nights"] = drop.round(0)
    return names.join(out, how="inner").sort_values(f"Drop {shortest}→{longest} nights")


# --- 1 · future demand calendar ----------------------------------------------------------


def latest_calendars(calendar: pd.DataFrame) -> pd.DataFrame:
    if calendar.empty:
        return calendar
    frame = calendar.copy()
    frame["collected_at"] = pd.to_datetime(frame["collected_at"], utc=True)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    newest = frame.groupby("listing_id")["collected_at"].transform("max")
    return frame[frame["collected_at"] == newest]


def future_demand(
    calendar: pd.DataFrame, listing_ids: set[str], today: date, days: int = 180
) -> pd.DataFrame:
    """Share of the comp set not bookable on every upcoming date."""
    latest = latest_calendars(calendar)
    if latest.empty:
        return pd.DataFrame(columns=["date", "unavailable_pct", "listings"])
    window = latest[
        latest["listing_id"].isin(listing_ids)
        & (latest["date"] >= today)
        & (latest["date"] < today + timedelta(days=days))
    ]
    daily = window.groupby("date").agg(
        unavailable_pct=("available", lambda s: (1 - s.mean()) * 100),
        listings=("listing_id", "nunique"),
    )
    daily = daily.reset_index()
    daily["unavailable_pct"] = daily["unavailable_pct"].round(0)
    return daily


NEAR_TERM_DAYS = 7


def busy_periods(demand: pd.DataFrame, top: int = 5, min_pct: float = 50) -> pd.DataFrame:
    """Runs of consecutive dates clearly busier than usual, busiest first.

    The next ``NEAR_TERM_DAYS`` are ignored: near dates always look full (last-minute
    bookings and blocks), which says nothing about demand.
    """
    if not demand.empty:
        first = min(demand["date"]) + timedelta(days=NEAR_TERM_DAYS)
        demand = demand[demand["date"] >= first]
    if demand.empty:
        return pd.DataFrame(columns=["From", "To", "Days", "Not bookable"])
    threshold = max(min_pct, float(demand["unavailable_pct"].quantile(0.85)))
    busy = demand[demand["unavailable_pct"] >= threshold].copy()
    if busy.empty:
        return pd.DataFrame(columns=["From", "To", "Days", "Not bookable"])
    dates = pd.to_datetime(busy["date"])
    busy["run"] = (dates.diff().dt.days != 1).cumsum()
    periods = busy.groupby("run").agg(
        From=("date", "min"),
        To=("date", "max"),
        Days=("date", "size"),
        **{"Not bookable": ("unavailable_pct", "mean")},
    )
    periods["Not bookable"] = periods["Not bookable"].round(0)
    return periods.sort_values("Not bookable", ascending=False).head(top).reset_index(drop=True)


def demand_by_month(demand: pd.DataFrame) -> pd.Series:
    if demand.empty:
        return pd.Series(dtype=float)
    months = pd.to_datetime(demand["date"]).dt.to_period("M").astype(str)
    return demand.groupby(months)["unavailable_pct"].mean().round(0)


# --- 3 · historical seasonality from reviews -------------------------------------------


def review_seasonality(
    reviews: pd.DataFrame, listing_ids: set[str], today: date, months: int = 24
) -> tuple[pd.Series, pd.Series]:
    """Reviews per month (last ``months``) and an index per calendar month (1.0 = average).

    Guests review right after checking out, so reviews trace completed stays over time.
    """
    if reviews.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    frame = reviews[reviews["listing_id"].isin(listing_ids)].copy()
    created = pd.to_datetime(frame["created_at"], utc=True).dt.tz_localize(None)
    end = pd.Timestamp(today.replace(day=1))
    start = end - pd.DateOffset(months=months)
    frame = frame[(created >= start) & (created < end)]
    period = created[frame.index].dt.to_period("M")
    full_range = pd.period_range(start, end - pd.DateOffset(days=1), freq="M")
    monthly = frame.groupby(period).size().reindex(full_range, fill_value=0)
    monthly.index = monthly.index.astype(str)
    by_month = monthly.groupby(pd.to_datetime(monthly.index).month).mean()
    index = (by_month / by_month.mean()).round(2) if by_month.mean() else by_month
    index.index = [pd.Timestamp(2000, m, 1).strftime("%b") for m in index.index]
    return monthly, index


# --- 4 · amenity premium ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Feature:
    label: str
    include: str
    exclude: str | None = None

    def present(self, names: list[str] | None) -> bool:
        for name in names or []:
            text = name.lower()
            if re.search(self.include, text) and not (
                self.exclude and re.search(self.exclude, text)
            ):
                return True
        return False


# Amenities that typically move prices, matched on their names (Airbnb's own codes mix
# things such as "Pool" and "Pool view").
FEATURES = (
    Feature("Pool", r"\bpool\b", r"view|table"),
    Feature("Gym", r"\bgym\b|exercise equipment"),
    Feature("Hot tub / jacuzzi", r"hot tub|jacuzzi"),
    Feature("Air conditioning", r"air conditioning"),
    Feature("Free parking", r"free .*(parking|garage)"),
    Feature("Washer", r"\bwasher\b", r"dish"),
    Feature("Dryer", r"\bdryer\b", r"hair"),
    Feature("Dedicated workspace", r"workspace"),
    Feature("Fast wifi", r"fast wifi"),
    Feature("Elevator", r"elevator"),
    Feature("Balcony / terrace", r"balcony|patio|terrace"),
    Feature("City or mountain view", r"(city|skyline|mountain) view"),
    Feature("Self check-in", r"self check-in|keypad|lockbox|smart lock"),
    Feature("Dishwasher", r"dishwasher"),
    Feature("Pets allowed", r"pets allowed"),
    Feature("Long-term stays allowed", r"long term stays"),
    Feature("Crib", r"\bcrib\b"),
    Feature("BBQ grill", r"bbq|barbecue|grill"),
)


def feature_matrix(listings: pd.DataFrame) -> pd.DataFrame:
    """One boolean column per feature, indexed by listing id."""
    data = {f.label: listings["amenities"].apply(f.present).to_numpy() for f in FEATURES}
    return pd.DataFrame(data, index=listings["listing_id"].to_numpy())


def amenity_premium(
    listings: pd.DataFrame, nightly: pd.Series, min_group: int = MIN_GROUP_SIZE
) -> pd.DataFrame:
    """How common each feature is among competitors and the median price with vs without."""
    comps = competitors(listings)
    if comps.empty:
        return pd.DataFrame()
    features = feature_matrix(comps)
    mine = my_listing(listings)
    my_features = (
        feature_matrix(listings[listings["is_mine"]]).iloc[0] if mine is not None else None
    )
    prices = nightly.reindex(features.index)
    bedrooms = comps.set_index("listing_id")["bedrooms"].reindex(features.index)
    rows = []
    for label in features.columns:
        has = features[label]
        with_price, without_price = prices[has].dropna(), prices[~has].dropna()
        comparable = len(with_price) >= min_group and len(without_price) >= min_group
        premium = _like_for_like_premium(prices, has, bedrooms) if comparable else None
        rows.append(
            {
                "Amenity": label,
                "Competitors with it": round(has.mean() * 100),
                "You": ("✓" if my_features[label] else "✗") if my_features is not None else "?",
                "Median price with": round(with_price.median()) if comparable else None,
                "Median price without": round(without_price.median()) if comparable else None,
                "Price difference (same bedrooms)": round(premium) if premium is not None else None,
                "Sample (with / without)": f"{len(with_price)} / {len(without_price)}",
                "Confidence": (
                    "Good"
                    if min(len(with_price), len(without_price)) >= CONFIDENT_GROUP_SIZE
                    else "Low"
                )
                if comparable
                else None,
            }
        )
    table = pd.DataFrame(rows)
    return table.sort_values(
        ["Price difference (same bedrooms)", "Competitors with it"],
        ascending=False,
        na_position="last",
    )


def _like_for_like_premium(prices: pd.Series, has: pd.Series, bedrooms: pd.Series) -> float | None:
    """Median price gap with vs without a feature, within each bedroom count, weighted.

    Comparing listings of the same size removes the most obvious bias (bigger listings
    tend to have more amenities and higher prices).
    """
    gaps: list[float] = []
    weights: list[int] = []
    for _, group in (
        pd.DataFrame({"price": prices, "has": has, "bedrooms": bedrooms})
        .dropna(subset=["price"])
        .groupby("bedrooms")
    ):
        with_price = group.loc[group["has"], "price"]
        without_price = group.loc[~group["has"], "price"]
        if len(with_price) >= 2 and len(without_price) >= 2:
            gaps.append(float(with_price.median()) / float(without_price.median()) - 1)
            weights.append(len(group))
    if not gaps:
        return None
    return sum(g * w for g, w in zip(gaps, weights, strict=True)) / sum(weights) * 100


# --- 5 · value map ----------------------------------------------------------------------


QUADRANTS = {
    (True, True): "Premium, well rated",
    (True, False): "Expensive for its rating",
    (False, True): "Good value",
    (False, False): "Budget",
}


def value_map(listings: pd.DataFrame, nightly: pd.Series) -> pd.DataFrame:
    """Listings with price and rating, labelled by quadrant around the comp set medians."""
    frame = listings[listings["details_collected_at"].notna() | listings["is_mine"]].copy()
    frame["nightly"] = frame["listing_id"].map(nightly)
    frame = frame.dropna(subset=["nightly", "rating"])
    comps = frame[~frame["is_mine"]]
    if comps.empty:
        return frame
    price_mid, rating_mid = comps["nightly"].median(), comps["rating"].median()
    frame["Quadrant"] = [
        QUADRANTS[(price >= price_mid, rating >= rating_mid)]
        for price, rating in zip(frame["nightly"], frame["rating"], strict=True)
    ]
    frame.attrs["price_mid"], frame.attrs["rating_mid"] = price_mid, rating_mid
    return frame


# --- 6 · minimum-stay segments ----------------------------------------------------------

SEGMENTS = ((0, 6, "Short stays (1–6 nights)"), (7, 19, "Weekly (7–19)"),
            (20, 10_000, "Monthly (20+)"))  # fmt: skip


def unavailable_share(calendar: pd.DataFrame, today: date, days: int) -> pd.Series:
    """Per listing: % of the next ``days`` that cannot be booked (latest calendar)."""
    latest = latest_calendars(calendar)
    window = latest[(latest["date"] >= today) & (latest["date"] < today + timedelta(days=days))]
    return ((1 - window.groupby("listing_id")["available"].mean()) * 100).round(0)


def min_stay_segments(
    listings: pd.DataFrame, nightly: pd.Series, unavailable: pd.Series
) -> pd.DataFrame:
    comps = competitors(listings).copy()
    if comps.empty:
        return pd.DataFrame()
    comps["nightly"] = comps["listing_id"].map(nightly)
    comps["unavailable"] = comps["listing_id"].map(unavailable)
    rows = []
    for low, high, label in SEGMENTS:
        group = comps[comps["minimum_nights"].between(low, high)]
        if group.empty:
            continue
        rows.append(
            {
                "Segment": label,
                "Competitors": len(group),
                "Median nightly price": _round(group["nightly"].median()),
                "Not bookable · next 60 days": _round(group["unavailable"].median()),
                "Median rating": round(group["rating"].median(), 2)
                if group["rating"].notna().any()
                else None,
                "Reviews (last 90 days, median)": _round(group["reviews_last_90_days"].median()),
            }
        )
    return pd.DataFrame(rows)


# --- 8 · pricing advisor ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signal:
    factor: str
    yours: str
    comparables: str
    effect: int  # -1 lowers, 0 neutral, +1 raises the target percentile


@dataclass(frozen=True, slots=True)
class PriceAdvice:
    nights: int
    comparables: pd.DataFrame
    p25: float
    median: float
    p75: float
    target_percentile: int
    suggested: float
    signals: tuple[Signal, ...]

    @property
    def monthly(self) -> float | None:
        return self.suggested * 30 if self.nights >= MONTHLY_STAY_NIGHTS else None


def comparables(
    listings: pd.DataFrame,
    nightly: pd.Series,
    *,
    max_km: float,
    same_bedrooms: bool,
    min_bathrooms: float | None,
) -> pd.DataFrame:
    """Priced competitors similar to your listing (every priced listing, not only analysed)."""
    mine = my_listing(listings)
    frame = listings[~listings["is_mine"]].copy()
    frame["nightly"] = frame["listing_id"].map(nightly)
    frame = frame.dropna(subset=["nightly"])
    frame = frame[frame["distance_km"].fillna(float("inf")) <= max_km]
    if mine is not None and same_bedrooms and pd.notna(mine["bedrooms"]):
        frame = frame[frame["bedrooms"] == mine["bedrooms"]]
    if min_bathrooms is not None:
        frame = frame[frame["bathrooms"].fillna(0) >= min_bathrooms]
    return frame


def advise_price(listings: pd.DataFrame, comps: pd.DataFrame, nights: int) -> PriceAdvice | None:
    """Suggested nightly price: a percentile of comparables, moved by your strengths.

    Starts at the median and moves one step (8 percentiles) per signal: rating, review
    count, amenities and price-moving features. Bounded to the P25–P75 range.
    """
    if len(comps) < MIN_COMPARABLES:
        return None
    prices = comps["nightly"]
    mine = my_listing(listings)
    signals = _signals(mine, comps, listings) if mine is not None else ()
    target = int(min(75, max(25, 50 + PERCENTILE_STEP * sum(s.effect for s in signals))))
    return PriceAdvice(
        nights=nights,
        comparables=comps,
        p25=float(prices.quantile(0.25)),
        median=float(prices.median()),
        p75=float(prices.quantile(0.75)),
        target_percentile=target,
        suggested=float(prices.quantile(target / 100)),
        signals=signals,
    )


def _signals(mine: pd.Series, comps: pd.DataFrame, listings: pd.DataFrame) -> tuple[Signal, ...]:
    signals: list[Signal] = []
    rating, comp_rating = mine.get("rating"), comps["rating"].median()
    if pd.notna(rating) and pd.notna(comp_rating):
        effect = 1 if rating >= comp_rating + 0.05 else -1 if rating <= comp_rating - 0.1 else 0
        signals.append(Signal("Guest rating", f"{rating:.2f}", f"{comp_rating:.2f}", effect))
    reviews, comp_reviews = mine.get("review_count"), comps["review_count"].median()
    if pd.notna(reviews) and pd.notna(comp_reviews):
        effect = -1 if reviews < min(10, comp_reviews / 2) else 0
        signals.append(Signal("Number of reviews (trust)", f"{reviews:.0f}",
                              f"{comp_reviews:.0f}", effect))  # fmt: skip
    analysed = comps[comps["amenities"].notna()]
    if mine.get("amenities") is not None and not analysed.empty:
        count = len(mine["amenities"])
        q25, q75 = analysed["amenities"].str.len().quantile([0.25, 0.75])
        effect = 1 if count >= q75 else -1 if count <= q25 else 0
        signals.append(Signal("Amenities listed", f"{count}", f"{q25:.0f}–{q75:.0f}", effect))
        mine_features = int(feature_matrix(listings[listings["is_mine"]]).iloc[0].sum())
        comp_features = feature_matrix(analysed).sum(axis=1).median()
        effect = (
            1
            if mine_features >= comp_features + 2
            else -1
            if mine_features <= comp_features - 2
            else 0
        )
        signals.append(Signal("Price-moving features (pool, gym, AC…)", f"{mine_features}",
                              f"{comp_features:.0f}", effect))  # fmt: skip
    return tuple(signals)


def seasonal_prices(pricing: pd.DataFrame, comps: pd.DataFrame, nights: int,
                    suggested: float) -> pd.DataFrame:  # fmt: skip
    """Suggested price per check-in month, following the comparables' seasonal pattern."""
    latest = latest_pricing(pricing)
    rows = latest[(latest["nights"] == nights) & latest["listing_id"].isin(comps["listing_id"])]
    if rows.empty:
        return pd.DataFrame()
    by_date = rows.groupby("check_in")["nightly_price"].median()
    factor = by_date / by_date.median()
    table = pd.DataFrame(
        {
            "Comparables priced": rows.groupby("check_in")["listing_id"].nunique(),
            "Comparables median": by_date.round(0),
            "Season factor": factor.round(2),
            "Suggested nightly after discount": (suggested * factor).round(0),
        }
    )
    if nights >= MONTHLY_STAY_NIGHTS:
        table["Suggested monthly"] = (table["Suggested nightly after discount"] * 30).round(0)
    table.index.name = "Check-in"
    return table


def deep_discounts(pricing: pd.DataFrame, listings: pd.DataFrame, min_pct: float) -> pd.DataFrame:
    """Latest prices at least ``min_pct`` below the listing's own original price."""
    latest = latest_pricing(pricing)
    deep = latest[latest["discount_pct"].fillna(0) >= min_pct]
    if deep.empty:
        return pd.DataFrame()
    deep = (
        deep.sort_values("discount_pct", ascending=False)
        .drop_duplicates(["listing_id", "nights"])
        .merge(listings[["listing_id", "title"]], on="listing_id", how="left")
    )
    return pd.DataFrame(
        {
            "listing_id": deep["listing_id"],
            "Listing": deep["title"],
            "Nights": deep["nights"],
            "Check-in": deep["check_in"],
            "Original nightly": (deep["original_price"] / deep["nights"]).round(0),
            "Nightly paid": deep["nightly_price"].round(0),
            "Discount": deep["discount_pct"].round(0),
        }
    )


def as_float(value: object) -> float | None:
    """A pandas cell as a plain float (None for missing or non-numeric values)."""
    try:
        number = float(str(value))
    except ValueError:
        return None
    return None if math.isnan(number) else number


def by_label(series: pd.Series) -> dict[object, float]:
    """A numeric Series as a plain {label: value} dict, skipping missing values."""
    return {k: v for k, v in ((k, as_float(v)) for k, v in series.items()) if v is not None}


def _round(value: float) -> int | None:
    return None if pd.isna(value) else round(value)


# --- pricing by check-in date (main pricing screen) ------------------------------------


def benchmarks_by_check_in(
    pricing: pd.DataFrame, comp_ids: set[str], nights: int, check_ins: list[str]
) -> pd.DataFrame:
    """Comparables' nightly price after discount for every check-in date searched.

    Dates searched without any priced comparable stay in the table (as "no data") so the
    admin sees the gap instead of a missing row.
    """
    latest = latest_pricing(pricing)
    rows = latest[(latest["nights"] == nights) & latest["listing_id"].isin(comp_ids)]
    grouped = rows.groupby("check_in")
    table = pd.DataFrame(
        {
            "median": grouped["nightly_price"].median(),
            "p25": grouped["nightly_price"].quantile(0.25),
            "p75": grouped["nightly_price"].quantile(0.75),
            "comparables": grouped["listing_id"].nunique(),
            "captured": grouped["collected_at"].max(),
        }
    )
    dates = sorted(set(check_ins) | set(table.index))
    return table.reindex(dates).rename_axis("check_in").reset_index()


def searched_check_ins(search_runs: pd.DataFrame, nights: int) -> list[str]:
    """Check-in dates of the latest search run for one stay length."""
    if search_runs.empty:
        return []
    latest_run = search_runs.sort_values("started_at")["run_id"].iloc[-1]
    runs = search_runs[(search_runs["run_id"] == latest_run) & (search_runs["nights"] == nights)]
    return sorted(runs["check_in"].unique())
