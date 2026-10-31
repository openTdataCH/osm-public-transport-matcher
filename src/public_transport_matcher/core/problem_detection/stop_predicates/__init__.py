"""Stop problem predicates — each function has signature (ctx, stop) -> list[ProblemResult]."""

from public_transport_matcher.core.problem_detection.stop_predicates.distance import distance_problem
from public_transport_matcher.core.problem_detection.stop_predicates.attributes import attributes_problem
from public_transport_matcher.core.problem_detection.stop_predicates.contradicts_route_matching import contradicts_route_matching_problem
from public_transport_matcher.core.problem_detection.stop_predicates.unmatched import unmatched_problem
from public_transport_matcher.core.problem_detection.stop_predicates.duplicates import duplicates_problem
from public_transport_matcher.core.problem_detection.stop_predicates.disused import disused_problem

__all__ = [
    "distance_problem",
    "attributes_problem",
    "contradicts_route_matching_problem",
    "unmatched_problem",
    "duplicates_problem",
    "disused_problem",
]
