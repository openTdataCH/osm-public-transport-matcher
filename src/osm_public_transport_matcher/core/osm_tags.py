"""The OSM tags that make an element a stop or a route a public transport service.

The Overpass query and the classification of parsed elements both read these
definitions, so what is downloaded and how it is classified cannot drift apart.
"""
from __future__ import annotations

import re

# Each selector is (key, operator, value) in Overpass syntax: '=' compares
# exactly, '~' searches a regular expression. The public_transport pattern is
# deliberately unanchored, as it always has been.
STOP_TAG_SELECTORS = (
    ('public_transport', '~', 'platform|stop_position|station|halt|stop'),
    ('railway', '=', 'tram_stop'),
    ('amenity', '=', 'ferry_terminal'),
    ('amenity', '=', 'bus_station'),
    ('highway', '=', 'bus_stop'),
    ('railway', '=', 'halt'),
    ('railway', '=', 'station'),
    ('aerialway', '=', 'station'),
)

# Keys whose value names the exact platform an element belongs to, such as
# ref:sloid=ch:1:sloid:7000:4:7. Platform ways are not stops for the matching
# rules, but a way with one of these keys is downloaded and kept so that its
# identifier can link it (see adapters/osm.py and IdentifierRule).
STOP_IDENTIFIER_KEYS = ('ref:sloid', 'railway:ref:sloid')

# OSM lifecycle prefixes for something that is no longer in service, such as
# disused:highway=bus_stop. Prefixes for future features (construction:,
# proposed:) are not included.
LIFECYCLE_PREFIXES = ('disused', 'abandoned', 'removed', 'demolished', 'razed', 'destroyed', 'was')

_STOP_KEYS = tuple(dict.fromkeys(key for key, _, _ in STOP_TAG_SELECTORS))
_STOP_VALUES = tuple(dict.fromkeys(
    value for _, _, pattern in STOP_TAG_SELECTORS for value in pattern.split('|')
))

# Overpass and Python regular expressions for a lifecycle-prefixed stop tag.
LIFECYCLE_KEY_PATTERN = f"^({'|'.join(LIFECYCLE_PREFIXES)}):({'|'.join(_STOP_KEYS)})$"
LIFECYCLE_VALUE_PATTERN = f"^({'|'.join(_STOP_VALUES)})$"

# route=* values of public transport services. Other route relations also pass
# through stops: infrastructure (railway, tracks) and leisure routes (piste,
# mtb, foot, ski...). They are not services and are left out.
PUBLIC_TRANSPORT_ROUTES = (
    'bus', 'trolleybus', 'minibus', 'share_taxi', 'coach', 'tram', 'light_rail',
    'train', 'subway', 'monorail', 'ferry', 'funicular', 'aerialway',
)
PUBLIC_TRANSPORT_ROUTE_PATTERN = f"^({'|'.join(PUBLIC_TRANSPORT_ROUTES)})$"

_LIFECYCLE_KEY = re.compile(LIFECYCLE_KEY_PATTERN)
_LIFECYCLE_VALUE = re.compile(LIFECYCLE_VALUE_PATTERN)


def has_active_stop_tag(tags: dict[str, str]) -> bool:
    """Whether an unprefixed tag still makes the element a stop for data users."""
    for key, operator, pattern in STOP_TAG_SELECTORS:
        value = tags.get(key)
        if value is None:
            continue
        if (operator == '=' and value == pattern) or (operator == '~' and re.search(pattern, value)):
            return True
    return False


def lifecycle_of(tags: dict[str, str]) -> str | None:
    """Return the lifecycle prefix of a retired stop tag, such as 'disused', or None.

    When several prefixes occur, the first in LIFECYCLE_PREFIXES wins.
    """
    found = set()
    for key, value in tags.items():
        match = _LIFECYCLE_KEY.match(key)
        if match and _LIFECYCLE_VALUE.match(value or ''):
            found.add(match.group(1))
    return next((prefix for prefix in LIFECYCLE_PREFIXES if prefix in found), None)


def is_public_transport_route(route: str | None, route_master: str | None = None) -> bool:
    """Whether a route relation is a public transport service.

    A relation without a ``route`` tag takes the ``route_master`` value of its
    route master; such relations are common in route masters of trams and buses.
    """
    return (route or route_master or '') in PUBLIC_TRANSPORT_ROUTES
