"""
The problem rules, in the order they run.

``api.py`` iterates ``STOP_PROBLEM_PIPELINE`` for unmatched entities so it can
attach per-node evidence; matched records use
``MatchRecord.evaluate_matched_problems()``.
"""

from osm_public_transport_matcher.core.problem_detection.stop_predicates import (
    distance_problem,
    attributes_problem,
    contradicts_route_matching_problem,
    unmatched_problem,
    duplicates_problem,
)

STOP_PROBLEM_PIPELINE = [
    distance_problem,
    attributes_problem,
    contradicts_route_matching_problem,
    unmatched_problem,
    duplicates_problem,
]
