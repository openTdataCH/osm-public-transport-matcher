"""Distance problem predicate — flags matched pairs where physical distance exceeds tolerance."""

from __future__ import annotations


from osm_public_transport_matcher.core.problem_detection.result import ProblemResult
from osm_public_transport_matcher.core.problem_detection.context import (
    ProblemContext,
)


def _compute_priority(distance_m, source_operator, profile):
    p1, p2, p3 = profile.distance_priorities
    exempt = str(source_operator or '').strip().upper() in profile.distance_priority_exempt_operators
    if distance_m > p1 and not exempt:
        return 1
    if distance_m > p2 and not exempt:
        return 2
    if distance_m > p3:
        return 3
    return None


from osm_public_transport_matcher.core.models import MatchRecord, SourceStop, OsmNode

def distance_problem(ctx: ProblemContext, record: MatchRecord | SourceStop | OsmNode) -> list[ProblemResult]:
    if not isinstance(record, MatchRecord):
        return []

    if record.distance_m is None:
        return []

    try:
        d = float(record.distance_m)
    except (ValueError, TypeError):
        return []

    # get source operator alias from source_node
    operator = record.source_node.operator
    priority = _compute_priority(d, operator, ctx.profile)
    if priority is None:
        return []

    return [ProblemResult(problem_type='distance', priority=priority)]
