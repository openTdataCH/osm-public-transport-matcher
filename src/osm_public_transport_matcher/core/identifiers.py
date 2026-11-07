"""Read the identifier tags of OSM elements against the source stops they name.

One reading serves the identifier rule, the linking of elements kept only for
their identifier, and the identifier problems, so all three agree on what a
value means.
"""
from __future__ import annotations

from dataclasses import dataclass

from osm_public_transport_matcher.core.models import OsmNode, SourceStop
from osm_public_transport_matcher.core.profile import IdentifierRule
from osm_public_transport_matcher.core.utils.common import haversine_distance


@dataclass(frozen=True)
class IdentifierReading:
    """What the identifier values of one OSM element name."""
    stops: tuple[tuple[SourceStop, str, float], ...] = ()   # (stop, value, distance_m) within reach
    far: tuple[tuple[SourceStop, str, float], ...] = ()     # the same, but beyond reach
    unknown: tuple[str, ...] = ()     # well formed, but no source stop is or extends it
    malformed: tuple[str, ...] = ()   # not an identifier of the rule's form

    @property
    def priority(self) -> int | None:
        """Problem priority: 1 for a far stop, 2 for an unknown value, 3 for a malformed one."""
        return 1 if self.far else 2 if self.unknown else 3 if self.malformed else None

    def evidence(self, rule: IdentifierRule) -> dict:
        return {
            'namespace': rule.namespace,
            'osm_tags': list(rule.osm_tags),
            'far': [{'value': value, 'source_key': stop.key, 'distance_m': round(distance, 1)}
                    for stop, value, distance in self.far],
            'unknown': list(self.unknown),
            'malformed': list(self.malformed),
        }


class IdentifierIndex:
    """The source stops of one identifier rule, by their identifier value."""

    def __init__(self, rule: IdentifierRule, source_stops):
        self.rule = rule
        self.stops: dict[str, SourceStop] = {}
        # A service point or area value (ch:1:sloid:7000) is known when a stop
        # identifier extends it after a colon (ch:1:sloid:7000:4:7).
        self.prefixes: set[str] = set()
        for stop in source_stops:
            if rule.source_namespace is not None and stop.namespace != rule.source_namespace:
                continue
            for reference in stop.references:
                if reference.namespace != rule.namespace:
                    continue
                self.stops.setdefault(reference.value, stop)
                parts = reference.value.split(':')
                self.prefixes.update(':'.join(parts[:end]) for end in range(1, len(parts) + 1))

    def read(self, node: OsmNode, max_distance: float) -> IdentifierReading | None:
        """Classify every identifier value of ``node``; None when it carries none."""
        values = self.rule.values(node.tags)
        if not values:
            return None
        stops, far, unknown, malformed = [], [], [], []
        for value in values:
            if not self.rule.is_well_formed(value):
                malformed.append(value)
                continue
            stop = self.stops.get(value)
            if stop is None:
                if value not in self.prefixes:
                    unknown.append(value)
                continue
            distance = haversine_distance(stop.lat, stop.lon, node.lat, node.lon)
            (stops if distance <= max_distance else far).append((stop, value, distance))
        return IdentifierReading(tuple(stops), tuple(far), tuple(unknown), tuple(malformed))
