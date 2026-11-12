"""Duplicates problem predicate — flags redundant source or OSM entries."""

from __future__ import annotations

from osm_public_transport_matcher.core.problem_detection.result import ProblemResult
from osm_public_transport_matcher.core.problem_detection.context import ProblemContext


from osm_public_transport_matcher.core.models import MatchRecord, SourceStop, OsmNode

OSM_DUPLICATE = ProblemResult(problem_type='duplicates', priority=3, has_osm_duplicate=True)
SOURCE_DUPLICATE = ProblemResult(problem_type='duplicates', priority=2, has_source_duplicate=True)


def duplicates_problem(ctx: ProblemContext, record: MatchRecord | SourceStop | OsmNode) -> list[ProblemResult]:
    """The duplicate that ``record`` reports, if any.

    A duplicate is a fact about one stop. An unmatched stop reports its own; a
    matched stop is reported by one of its matches, chosen by the context.
    """
    if isinstance(record, MatchRecord):
        if ctx.duplicate_report_by_osm.get(str(record.osm_node.node_id)) is record:
            return [OSM_DUPLICATE]
        if ctx.duplicate_report_by_source.get(str(record.source_node.key)) is record:
            return [SOURCE_DUPLICATE]
    elif isinstance(record, SourceStop):
        if str(record.key) in ctx.duplicate_key_map:
            return [SOURCE_DUPLICATE]
    elif str(record.node_id) in ctx.duplicate_osm_node_ids:
        return [OSM_DUPLICATE]
    return []
