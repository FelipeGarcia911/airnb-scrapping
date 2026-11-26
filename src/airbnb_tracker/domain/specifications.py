"""Composable rules that decide whether a listing counts as a competitor.

Specification pattern built on the *Composite* structure: leaf rules can be combined
with ``&`` and ``~`` so new criteria are added without touching existing code (OCP).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from airbnb_tracker.domain.entities import Listing, RoomType
from airbnb_tracker.domain.property_types import PropertyGroup, property_group
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId


class ListingSpecification(ABC):
    @abstractmethod
    def is_satisfied_by(self, listing: Listing) -> bool: ...

    def __and__(self, other: ListingSpecification) -> ListingSpecification:
        return AllOf(self, other)

    def __invert__(self) -> ListingSpecification:
        return Not(self)


class AnyListing(ListingSpecification):
    """Null Object: accepts every listing."""

    def is_satisfied_by(self, listing: Listing) -> bool:
        return True


class AllOf(ListingSpecification):
    def __init__(self, *specifications: ListingSpecification) -> None:
        self._specifications = specifications

    def is_satisfied_by(self, listing: Listing) -> bool:
        return all(spec.is_satisfied_by(listing) for spec in self._specifications)


class Not(ListingSpecification):
    def __init__(self, specification: ListingSpecification) -> None:
        self._specification = specification

    def is_satisfied_by(self, listing: Listing) -> bool:
        return not self._specification.is_satisfied_by(listing)


class HasRoomType(ListingSpecification):
    def __init__(self, *room_types: RoomType) -> None:
        self._room_types = frozenset(room_types)

    def is_satisfied_by(self, listing: Listing) -> bool:
        return listing.room_type in self._room_types


class MinBedrooms(ListingSpecification):
    """Listings whose bedroom count is unknown are kept unless ``keep_unknown`` is False."""

    def __init__(self, minimum: int, *, keep_unknown: bool = True) -> None:
        self._minimum = minimum
        self._keep_unknown = keep_unknown

    def is_satisfied_by(self, listing: Listing) -> bool:
        if listing.bedrooms is None:
            return self._keep_unknown
        return listing.bedrooms >= self._minimum


class MaxBedrooms(ListingSpecification):
    """Airbnb has no "maximum bedrooms" filter, so it is applied after searching."""

    def __init__(self, maximum: int, *, keep_unknown: bool = True) -> None:
        self._maximum = maximum
        self._keep_unknown = keep_unknown

    def is_satisfied_by(self, listing: Listing) -> bool:
        if listing.bedrooms is None:
            return self._keep_unknown
        return listing.bedrooms <= self._maximum


class MinRating(ListingSpecification):
    """New listings without rating are kept unless ``keep_unrated`` is False."""

    def __init__(self, minimum: float, *, keep_unrated: bool = True) -> None:
        self._minimum = minimum
        self._keep_unrated = keep_unrated

    def is_satisfied_by(self, listing: Listing) -> bool:
        if listing.rating is None:
            return self._keep_unrated
        return listing.rating >= self._minimum


class WithinRadius(ListingSpecification):
    def __init__(self, center: GeoPoint, radius_km: float) -> None:
        self._center = center
        self._radius_km = radius_km

    def is_satisfied_by(self, listing: Listing) -> bool:
        if listing.location is None:
            return False
        return self._center.distance_km_to(listing.location) <= self._radius_km


class ExcludeIds(ListingSpecification):
    def __init__(self, listing_ids: Iterable[ListingId]) -> None:
        self._ids = frozenset(listing_ids)

    def is_satisfied_by(self, listing: Listing) -> bool:
        return listing.id not in self._ids


class InPropertyGroups(ListingSpecification):
    """Same kind of property (apartments, houses, rural stays…)."""

    def __init__(self, groups: Iterable[PropertyGroup]) -> None:
        self._groups = frozenset(groups)

    def is_satisfied_by(self, listing: Listing) -> bool:
        return property_group(listing.property_type) in self._groups
