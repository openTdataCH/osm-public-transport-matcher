"""The OSM tags that make an element a stop, and the lifecycle prefixes that retire one.

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
