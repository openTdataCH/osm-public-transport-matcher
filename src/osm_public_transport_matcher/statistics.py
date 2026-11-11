"""Pure quality summaries computed with a matching run, never by the review app."""
import math
import logging
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

def _classify_match_type(match_type: str) -> str:
    """Quality metrics are reported per match type; each one names its rule and branch."""
    return match_type or 'other'


def _distance_stats(distances: List[float]) -> Dict[str, Any]:
    """Compute mean, median, p95 for a list of distances."""
    if not distances:
        return {"mean_m": None, "median_m": None, "p95_m": None, "count": 0}
    distances_sorted = sorted(distances)
    p95_idx = int(math.ceil(0.95 * len(distances_sorted))) - 1
    return {
        "mean_m": round(statistics.mean(distances_sorted), 2),
        "median_m": round(statistics.median(distances_sorted), 2),
        "p95_m": round(distances_sorted[max(0, p95_idx)], 2),
        "count": len(distances_sorted),
    }


def compute_quality_metrics(
    matched_records: list,
    all_osm_nodes: list,
    osm_stop_units: list | None = None,
) -> Dict[str, Any]:
    """Compute matching quality metrics from pipeline results.

    Builds a temporary KDTree of all OSM nodes to evaluate whether each
    matched pair uses the nearest available OSM node.
    """
    from scipy.spatial import KDTree
    from osm_public_transport_matcher.core.utils.spatial_index import batch_to_xyz, to_xyz

    # ------------------------------------------------------------------
    # 1. Distance quality
    # ------------------------------------------------------------------
    all_distances: List[float] = []
    distances_by_stage: Dict[str, List[float]] = defaultdict(list)

    for rec in matched_records:
        d = getattr(rec, 'distance_m', None)
        if d is not None and not math.isnan(d):
            all_distances.append(d)
            stage = _classify_match_type(getattr(rec, 'match_type', '') or '')
            distances_by_stage[stage].append(d)

    overall_dist = _distance_stats(all_distances)
    by_stage_dist = {stage: _distance_stats(dists) for stage, dists in sorted(distances_by_stage.items())}

    # Build KDTree for "not matched to closest" and cross-predicate consistency
    osm_coords: List[Tuple[float, float]] = []
    for node in all_osm_nodes:
        lat, lon = getattr(node, 'lat', None), getattr(node, 'lon', None)
        if lat is not None and lon is not None :
            osm_coords.append((float(lat), float(lon)))

    not_closest_count = 0
    consistent_count = 0
    total_evaluated = 0
    not_closest_by_stage: Dict[str, int] = {}

    if osm_coords:
        osm_xyz = batch_to_xyz(osm_coords)
        tree = KDTree(osm_xyz)

        for rec in matched_records:
            source_node = getattr(rec, 'source_node', None)
            osm_node = getattr(rec, 'osm_node', None)
            if source_node is None or osm_node is None:
                continue
            a_lat, a_lon = getattr(source_node, 'lat', None), getattr(source_node, 'lon', None)
            if a_lat is None or a_lon is None:
                continue

            query_point = to_xyz(a_lat, a_lon)
            nearest_distance, _ = tree.query(query_point, k=1)
            
            stage = _classify_match_type(getattr(rec, 'match_type', '') or '')

            total_evaluated += 1
            matched_point = to_xyz(osm_node.lat, osm_node.lon)
            matched_distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(query_point, matched_point)))
            if matched_distance <= nearest_distance + 1e-12:
                consistent_count += 1
            else:
                not_closest_count += 1
                not_closest_by_stage[stage] = not_closest_by_stage.get(stage, 0) + 1

    not_closest_pct = round(not_closest_count / total_evaluated * 100, 1) if total_evaluated else 0.0
    consistency_pct = round(consistent_count / total_evaluated * 100, 1) if total_evaluated else 0.0

    distance_quality = {
        "overall": overall_dist,
        "by_stage": by_stage_dist,
        "not_matched_to_closest": {
            "count": not_closest_count,
            "total_evaluated": total_evaluated,
            "percent": not_closest_pct,
        },
        "not_matched_to_closest_by_stage": not_closest_by_stage,
    }

    # ------------------------------------------------------------------
    # 2. Many-to-one analysis
    # ------------------------------------------------------------------
    node_to_stop_id: Dict[str, str] = {}
    for stop_idx, stop_unit in enumerate(osm_stop_units or []):
        for member in getattr(stop_unit, 'members', []) or []:
            node_id = getattr(member, 'node_id', None)
            if node_id:
                node_to_stop_id[str(node_id)] = str(stop_idx)

    source_to_osm: Dict[str, set] = defaultdict(set)
    osm_to_source: Dict[str, set] = defaultdict(set)

    for rec in matched_records:
        source_key = getattr(getattr(rec, 'source_node', None), 'key', None)
        osm_id = getattr(getattr(rec, 'osm_node', None), 'node_id', None)
        if source_key and osm_id:
            canonical_osm_stop = node_to_stop_id.get(str(osm_id), f"node:{osm_id}")
            source_to_osm[str(source_key)].add(canonical_osm_stop)
            osm_to_source[canonical_osm_stop].add(str(source_key))

    source_multi = {s: ids for s, ids in source_to_osm.items() if len(ids) > 1}
    osm_multi = {n: ids for n, ids in osm_to_source.items() if len(ids) > 1}

    def distribution(groups, size_field):
        sizes = defaultdict(int)
        for ids in groups.values():
            sizes[len(ids)] += 1
        return [{size_field: size, "count": count} for size, count in sorted(sizes.items())]

    many_to_one = {
        "source_to_multiple_osm": {
            "count": len(source_multi),
            "max_per_source": max((len(v) for v in source_multi.values()), default=0),
            "distribution": distribution(source_multi, "osm_count"),
        },
        "osm_to_multiple_source": {
            "count": len(osm_multi),
            "max_per_osm": max((len(v) for v in osm_multi.values()), default=0),
            "distribution": distribution(osm_multi, "source_count"),
        },
    }

    # ------------------------------------------------------------------
    # 3. Cross-predicate consistency (reuses KDTree results from above)
    # ------------------------------------------------------------------
    cross_predicate = {
        "consistent_with_nearest": consistent_count,
        "would_differ_by_nearest": not_closest_count,
        "total_evaluated": total_evaluated,
        "consistency_percent": consistency_pct,
    }

    logger.info(
        f"Quality metrics: consistency={consistency_pct}%, "
        f"not_closest={not_closest_count}, many_to_one_source={len(source_multi)}"
    )

    return {
        "distance_quality": distance_quality,
        "many_to_one": many_to_one,
        "cross_predicate_consistency": cross_predicate,
    }

