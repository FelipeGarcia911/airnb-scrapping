"""Groups of Airbnb property types that compete with each other.

A city apartment does not compete with a country estate even at the same bedroom count:
mixing them inflates prices far more than any data error. Airbnb labels listings with
many property types ("Apartment", "Condo", "Farm stay"…); they are grouped here.
"""

from __future__ import annotations

from enum import StrEnum


class PropertyGroup(StrEnum):
    APARTMENT = "apartment"
    HOUSE = "house"
    RURAL = "rural"
    OTHER = "other"

    @property
    def label(self) -> str:
        return {
            PropertyGroup.APARTMENT: "Apartments (apartment, condo, loft, rental unit)",
            PropertyGroup.HOUSE: "Houses (home, townhouse, guesthouse, vacation home)",
            PropertyGroup.RURAL: "Rural & nature stays (cabin, cottage, villa, farm stay…)",
            PropertyGroup.OTHER: "Other (hotel rooms, unusual stays…)",
        }[self]


_GROUPS: dict[PropertyGroup, tuple[str, ...]] = {
    PropertyGroup.APARTMENT: (
        "apartment", "condo", "loft", "rental unit", "serviced apartment", "aparthotel",
        "guest suite", "penthouse", "flat", "studio",
    ),
    PropertyGroup.HOUSE: (
        "home", "house", "townhouse", "guesthouse", "vacation home", "casa particular",
        "bungalow",
    ),
    PropertyGroup.RURAL: (
        "cabin", "cottage", "villa", "farm stay", "farm", "chalet", "ranch", "earthen home",
        "tiny home", "treehouse", "dome", "hut", "barn", "yurt", "tent", "campsite", "finca",
        "nature lodge", "country house",
    ),
}  # fmt: skip


def property_group(property_type: str | None) -> PropertyGroup:
    """Group of an Airbnb property type, e.g. "Rental unit" -> APARTMENT."""
    text = (property_type or "").strip().lower()
    for group, names in _GROUPS.items():
        if text in names:
            return group
    for group, names in _GROUPS.items():  # longer labels such as "Entire rental unit"
        if any(name in text for name in names):
            return group
    return PropertyGroup.OTHER
