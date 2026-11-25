from tests.conftest import CENTER, make_listing

from airbnb_tracker.domain.entities import RoomType
from airbnb_tracker.domain.specifications import (
    AnyListing,
    ExcludeIds,
    HasRoomType,
    MaxBedrooms,
    MinBedrooms,
    MinRating,
    WithinRadius,
)
from airbnb_tracker.domain.value_objects import GeoPoint, ListingId


def test_any_listing_accepts_everything() -> None:
    assert AnyListing().is_satisfied_by(make_listing())


def test_min_bedrooms_keeps_unknown_by_default() -> None:
    spec = MinBedrooms(2)
    assert spec.is_satisfied_by(make_listing(bedrooms=2))
    assert not spec.is_satisfied_by(make_listing(bedrooms=1))
    assert spec.is_satisfied_by(make_listing(bedrooms=None))
    assert not MinBedrooms(2, keep_unknown=False).is_satisfied_by(make_listing(bedrooms=None))


def test_max_bedrooms() -> None:
    spec = MaxBedrooms(3)
    assert spec.is_satisfied_by(make_listing(bedrooms=3))
    assert not spec.is_satisfied_by(make_listing(bedrooms=4))
    assert spec.is_satisfied_by(make_listing(bedrooms=None))


def test_min_rating_keeps_new_listings() -> None:
    spec = MinRating(4.5)
    assert spec.is_satisfied_by(make_listing(rating=4.9))
    assert not spec.is_satisfied_by(make_listing(rating=4.0))
    assert spec.is_satisfied_by(make_listing(rating=None))


def test_within_radius() -> None:
    spec = WithinRadius(CENTER, 1.0)
    assert spec.is_satisfied_by(make_listing(location=CENTER))
    assert not spec.is_satisfied_by(make_listing(location=GeoPoint(6.30, -75.56)))
    assert not spec.is_satisfied_by(make_listing(location=None))


def test_composition_with_operators() -> None:
    spec = HasRoomType(RoomType.ENTIRE_PLACE) & ~ExcludeIds([ListingId("1")])
    assert spec.is_satisfied_by(make_listing("1"))
    assert not spec.is_satisfied_by(make_listing("2"))
    assert not spec.is_satisfied_by(make_listing("1", room_type=RoomType.PRIVATE_ROOM))
