"""
Problem Detection Package

Predicate-based pipeline for detecting and prioritizing data quality issues
in matched/unmatched source↔OSM stop data.
"""

from osm_public_transport_matcher.core.problem_detection.context import ProblemContext
from osm_public_transport_matcher.core.problem_detection.pipeline import STOP_PROBLEM_PIPELINE

__all__ = [
    "ProblemContext",
    "STOP_PROBLEM_PIPELINE",
]
