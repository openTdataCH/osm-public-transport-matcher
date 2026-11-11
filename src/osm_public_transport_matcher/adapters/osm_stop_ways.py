"""Which OSM ways are stops: the one rule both OSM readers follow.

``read_osm()`` builds the OSM stops for stop matching and
``process_osm_routes_data()`` turns route relation members into stop calls.
Both ask this module which ways are stops and where they are, so the two
halves of the engine cannot disagree about whether a station way exists.
"""
from __future__ import annotations

from dataclasses import dataclass

from osm_public_transport_matcher.core.osm_tags import STOP_IDENTIFIER_KEYS, lifecycle_of


@dataclass(frozen=True)
class StopWay:
    """A way kept as a stop, placed at its centre point."""

    way_id: str
    tags: dict[str, str]
    lat: float
    lon: float
    # Kept only for its stop identifier (a platform with ref:sloid); see stop_ways().
    identifier_only: bool

    @property
    def virtual_id(self) -> str:
        return f"way_{self.way_id}"


def _tags_of(element) -> dict[str, str]:
    return {tag.get('k'): tag.get('v') for tag in element.findall('tag')}


def _coordinates(element) -> tuple[float, float] | None:
    try:
        return float(element.get('lat')), float(element.get('lon'))
    except (TypeError, ValueError):
        return None


def stop_ways(root, identifier_keys: tuple[str, ...] = STOP_IDENTIFIER_KEYS) -> list[StopWay]:
    """Return the ways of an OSM XML tree that are stops, in document order.

    A way is a stop when it is

    1. ``aerialway=station`` with ``public_transport=station``, or
    2. carries a ``uic_ref`` that no active node already carries: a disused
       node (lifecycle-prefixed stop tag) never hides such a way.

    Any other way with one of ``identifier_keys`` is kept ``identifier_only``.

    A way is placed at its Overpass ``<center>``; without one, at the mean of
    its member nodes that have coordinates. A way with neither has no
    supported geometry and is not a stop. Only nodes with coordinates count,
    both for hiding a way and for placing one.
    """
    active_node_uic_refs: set[str] = set()
    node_coordinates: dict[str, tuple[float, float]] = {}
    for node in root.iter('node'):
        coordinates = _coordinates(node)
        if coordinates is None:
            continue
        node_coordinates[node.get('id')] = coordinates
        tags = _tags_of(node)
        if tags.get('uic_ref') and not lifecycle_of(tags):
            active_node_uic_refs.add(tags['uic_ref'])

    ways: list[StopWay] = []
    for way in root.iter('way'):
        way_id = way.get('id')
        if not way_id:
            continue
        tags = _tags_of(way)
        is_aerialway_station = tags.get('aerialway') == 'station' and tags.get('public_transport') == 'station'
        uic_ref = tags.get('uic_ref')
        is_uic_without_node = bool(uic_ref) and uic_ref not in active_node_uic_refs
        identifier_only = not (is_aerialway_station or is_uic_without_node) and any(
            tags.get(key) for key in identifier_keys)
        if not (is_aerialway_station or is_uic_without_node or identifier_only):
            continue

        center = way.find('center')
        coordinates = _coordinates(center) if center is not None else None
        if coordinates is None:
            members = [node_coordinates[nd.get('ref')] for nd in way.findall('nd') if nd.get('ref') in node_coordinates]
            if not members:
                continue
            coordinates = (sum(lat for lat, _ in members) / len(members), sum(lon for _, lon in members) / len(members))
        ways.append(StopWay(way_id, tags, coordinates[0], coordinates[1], identifier_only))
    return ways
