"""Generated cases check identity conservation, serialization, and allocation."""
import gzip
import itertools
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st
import pytest

from transport_matcher import SourceStop, OsmNode, match, write_bundle
from transport_matcher.core.predicates.distance_matching import bipartite_match_max_cardinality
from transport_matcher.core.utils.common import haversine_distance
from transport_matcher.results import InvalidResult, validate_bundle, validate_output
from transport_matcher.results.bundle import _json_value, _sections
from transport_matcher.results.validation import validate_records

IDENTIFIER = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=20)


@settings(max_examples=35, deadline=None, derandomize=True)
@given(namespace=IDENTIFIER, source_id=IDENTIFIER, osm_id=st.integers(1, 10**12),
       kind=st.sampled_from(["node", "way", "relation"]),
       lat=st.floats(min_value=-80, max_value=80, allow_nan=False, allow_infinity=False),
       lon=st.floats(min_value=-170, max_value=170, allow_nan=False, allow_infinity=False))
def test_bundle_roundtrip_preserves_namespaced_and_typed_identity(namespace, source_id, osm_id, kind, lat, lon):
    result = match([SourceStop(namespace, source_id, lat, lon, name="Shared stop")],
                   [OsmNode(str(osm_id), lat, lon, name="Shared stop", element_type=kind)])
    with TemporaryDirectory() as temporary:
        bundle = write_bundle(result, Path(temporary) / "result")
        manifest = validate_bundle(bundle)
        assert manifest["files"]["matches"]["rows"] == 1
        with gzip.open(bundle / "matches.jsonl.gz", "rt", encoding="utf-8") as handle:
            row = json.loads(handle.readline())
        assert row["source_key"] == f"{namespace}:{source_id}"
        assert row["osm_key"] == f"osm:{kind}:{osm_id}"


@settings(max_examples=45, deadline=None, derandomize=True)
@given(source_count=st.integers(1, 8), osm_count=st.integers(0, 8))
def test_matching_conserves_complete_input_identity_sets(source_count, osm_count):
    sources = [SourceStop("property", str(i), 47 + i / 100, 8, name=f"Stop {i}") for i in range(source_count)]
    nodes = [OsmNode(str(i), 47 + i / 100, 8, name=f"Stop {i}") for i in range(osm_count)]
    result = match(sources, nodes)
    matched_source = {pair.source_node.key for pair in result.matched}
    matched_osm = {pair.osm_node.key for pair in result.matched}
    unmatched_source = {stop.key for stop in result.unmatched_source}
    unmatched_osm = {node.key for node in result.unmatched_osm}
    assert matched_source.isdisjoint(unmatched_source)
    assert matched_osm.isdisjoint(unmatched_osm)
    assert matched_source | unmatched_source == {stop.key for stop in sources}
    assert matched_osm | unmatched_osm == {node.key for node in nodes}
    assert all(math.isfinite(pair.distance_m) and pair.distance_m >= 0 for pair in result.matched)
    validate_output(result)


@settings(max_examples=45, deadline=None, derandomize=True)
@given(source_offsets=st.lists(st.integers(-5, 5), min_size=1, max_size=4),
       osm_offsets=st.lists(st.integers(-5, 5), min_size=1, max_size=4),
       cap=st.integers(1, 90))
def test_distance_allocator_reaches_feasible_maximum_without_reusing_entities(source_offsets, osm_offsets, cap):
    sources = [SourceStop("p", str(i), 47 + offset / 10000, 8) for i, offset in enumerate(source_offsets)]
    nodes = [OsmNode(str(i), 47 + offset / 10000, 8) for i, offset in enumerate(osm_offsets)]
    pairs = bipartite_match_max_cardinality(sources, nodes, cap)
    assert len({source for source, _, _ in pairs}) == len(pairs)
    assert len({osm for _, osm, _ in pairs}) == len(pairs)
    assert all(0 <= distance <= cap for _, _, distance in pairs)
    edges = {(i, j) for i, source in enumerate(sources) for j, node in enumerate(nodes)
             if haversine_distance(source.lat, source.lon, node.lat, node.lon) <= cap}
    optimum = 0
    # Exhaustive oracle is intentionally independent of the optimizer.
    for size in range(1, min(len(sources), len(nodes)) + 1):
        for left in itertools.combinations(range(len(sources)), size):
            if any(all(pair in edges for pair in zip(left, right))
                   for right in itertools.permutations(range(len(nodes)), size)):
                optimum = size
    assert len(pairs) == optimum


@settings(max_examples=30, deadline=None, derandomize=True)
@given(invalid_lat=st.one_of(st.sampled_from([True, False, None, "47", float("nan"), float("inf")]),
                           st.floats(min_value=90.0001, max_value=1e10),
                           st.floats(min_value=-1e10, max_value=-90.0001)))
def test_wire_schema_rejects_non_numeric_nonfinite_and_out_of_range_positions(invalid_lat):
    result = match([SourceStop("p", "one", 47, 8)], [])
    records = {section: [_json_value(row) for row in rows] for section, rows in _sections(result).items()}
    records["source_stops"][0]["lat"] = invalid_lat
    with pytest.raises(InvalidResult, match="coordinates"):
        validate_records(records)


@pytest.mark.parametrize("coordinate,endpoint", [("lat", -90.0), ("lat", 90.0), ("lon", -180.0), ("lon", 180.0)])
def test_wire_coordinate_domain_includes_endpoints_but_not_adjacent_values(coordinate, endpoint):
    result = match([SourceStop("p", "one", 0, 0)], [])
    records = {section: [_json_value(row) for row in rows] for section, rows in _sections(result).items()}
    records["source_stops"][0][coordinate] = endpoint
    validate_records(records)
    records["source_stops"][0][coordinate] = math.nextafter(endpoint, math.copysign(math.inf, endpoint))
    with pytest.raises(InvalidResult, match="coordinates"):
        validate_records(records)
