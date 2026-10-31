"""Disused problem predicate — OSM elements retired with a lifecycle prefix that still need attention."""

from __future__ import annotations

from public_transport_matcher.core.models import MatchRecord, OsmNode, SourceStop
from public_transport_matcher.core.osm_tags import has_active_stop_tag
from public_transport_matcher.core.problem_detection.context import ProblemContext
from public_transport_matcher.core.problem_detection.result import ProblemResult


def conflicting_source_stops(ctx: ProblemContext, record: OsmNode) -> list[SourceStop]:
    """Unmatched source stops that share the element's station reference."""
    candidates = ctx.unmatched_source_by_station_ref.get(str(record.station_ref or ''), [])
    return [stop for stop in candidates if ctx.profile.station_references_compatible(stop, record)]


def disused_problem(ctx: ProblemContext, record: MatchRecord | SourceStop | OsmNode) -> list[ProblemResult]:
    if not isinstance(record, OsmNode) or not record.lifecycle:
        return []
    # P1: the source still lists the station, with a stop that has no OSM partner.
    if conflicting_source_stops(ctx, record):
        return [ProblemResult(problem_type='disused', priority=1)]
    # P2: data users still see a stop: an unprefixed stop tag remains, or a route lists it.
    if has_active_stop_tag(record.tags) or ctx.osm_node_routes.get(record.node_id):
        return [ProblemResult(problem_type='disused', priority=2)]
    return []
