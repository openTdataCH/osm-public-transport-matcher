"""Standalone Swiss runner: explicit input paths, complete in-memory results."""
from __future__ import annotations

import json
import os
import re
from osm_public_transport_matcher.timing import stage
from pathlib import Path

import pandas as pd

from osm_public_transport_matcher.api import match
from osm_public_transport_matcher.progress import progress_stage
from osm_public_transport_matcher.core.models import MatchingOutput
from osm_public_transport_matcher.core.route_state import RouteState
from osm_public_transport_matcher.profiles import SWITZERLAND
from osm_public_transport_matcher.profiles.switzerland import normalize_route_id
from osm_public_transport_matcher.routes import compare_routes
from osm_public_transport_matcher.adapters.atlas import atlas_records, atlas_state
from osm_public_transport_matcher.adapters.metadata import fingerprint
from osm_public_transport_matcher.adapters.osm import read_osm
from osm_public_transport_matcher.adapters.osm_routes import process_osm_routes_data
from osm_public_transport_matcher.adapters.route_products import (
    load_source_route_products, read_gtfs_identity_cache, scope_source_stop_keys, unresolved_osm_member_diagnostics,
)


def timetable_line_number(ref: str) -> str | None:
    """The line number in a Swiss timetable number: OSM often tags bus line 158 as 31.158."""
    match = re.fullmatch(r'\d{2}\.(\d{3})', ref)
    return (match.group(1).lstrip('0') or '0') if match else None


def _boundary_selector(path: Path):
    from shapely.geometry import shape
    from shapely import covers, points
    from shapely.ops import unary_union
    from osm_public_transport_matcher.adapters.get_atlas_gtfs import check_swiss_stop_columns, check_swiss_stops_found
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['type'] == 'FeatureCollection':
        polygon = unary_union([shape(item['geometry']) for item in data['features']])
    elif data['type'] == 'Feature':
        polygon = shape(data['geometry'])
    else:
        polygon = shape(data)
    def select(frame):
        check_swiss_stop_columns(frame)
        valid = frame.dropna(subset=['stop_lat', 'stop_lon']).copy()
        prefixes = (
            valid['didok'].fillna('').astype(str).str.startswith('85')
            | valid['original_stop_id'].fillna('').astype(str).str.startswith('85')
            | valid['stop_id'].fillna('').astype(str).str.startswith('85')
        )
        valid = valid[prefixes].copy()
        selected = valid[covers(polygon, points(valid['stop_lon'].to_numpy(dtype=float), valid['stop_lat'].to_numpy(dtype=float)))].copy()
        check_swiss_stops_found(selected)
        return selected
    return select


def _load_gtfs_products(gtfs_dir, boundary_geojson, stage_dir):
    selector = _boundary_selector(Path(boundary_geojson)) if boundary_geojson else lambda frame: frame.copy()
    backend = os.getenv('GTFS_PROCESSING_BACKEND', 'duckdb')
    if backend == 'duckdb':
        from osm_public_transport_matcher.adapters.gtfs_columnar import load_gtfs_columnar
        return load_gtfs_columnar(gtfs_dir, stop_selector=selector, stage_dir=stage_dir)
    if backend == 'pandas':
        from osm_public_transport_matcher.adapters.get_atlas_gtfs import load_gtfs_data_streaming
        with stage('gtfs.pandas_scan'):
            return load_gtfs_data_streaming(str(gtfs_dir), stop_selector=selector, stage_dir=str(stage_dir))
    raise ValueError('GTFS_PROCESSING_BACKEND must be duckdb or pandas')


def _map_gtfs_products(gtfs, traffic_points):
    from osm_public_transport_matcher.adapters.get_atlas_gtfs import (
        build_gtfs_atlas_payload, build_integrated_gtfs_data_streaming, build_gtfs_db_payload_rows,
    )
    from osm_public_transport_matcher.adapters.get_atlas_data import swiss_route_products
    with stage('gtfs.identity_matching'):
        identity = build_gtfs_atlas_payload(gtfs, traffic_points)
        integrated = build_integrated_gtfs_data_streaming(gtfs, traffic_points, gtfs_payload=identity)
    with stage('gtfs.itineraries'):
        route_products = swiss_route_products(gtfs, integrated)
    with stage('gtfs.identity_export'):
        stop_rows, state_rows = build_gtfs_db_payload_rows(identity, traffic_points)
    return route_products, stop_rows, state_rows, identity['mapping_stats_export']


def _fresh_gtfs_products(gtfs_dir, traffic_points, boundary_geojson):
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix="osm-public-transport-matcher-gtfs-") as stage_dir:
        return _map_gtfs_products(_load_gtfs_products(gtfs_dir, boundary_geojson, stage_dir), traffic_points)


def run_matching(
    atlas_csv: str | Path,
    osm_xml: str | Path,
    processed_dir: str | Path | None = None,
    gtfs_dir: str | Path | None = None,
    *,
    boundary_geojson: str | Path | None = None,
) -> MatchingOutput:
    """Match a Swiss snapshot without application imports, credentials or writes.

    ``processed_dir`` optionally supplies cached source route/identity products.
    OSM route products are always derived from this run's XML, avoiding stale
    relation data. ``gtfs_dir`` refreshes GTFS identity and routes; without an
    explicit ``boundary_geojson``, its stop table is treated as preselected input.
    Neither matching nor boundary selection downloads data.
    """
    atlas_path, osm_path = Path(atlas_csv), Path(osm_xml)
    with progress_stage('atlas.load'):
        traffic_points = pd.read_csv(atlas_path, sep=';', dtype=str)
        records = atlas_records(traffic_points)
    route_data = {}
    stop_rows, identity_rows = [], []
    source_metadata = {}
    input_paths = [atlas_path, osm_path]
    if processed_dir is not None:
        with progress_stage('timetable.load'):
            if gtfs_dir is None:
                route_data = load_source_route_products(processed_dir)
                stop_rows, identity_rows = read_gtfs_identity_cache(processed_dir)
            processed_path = Path(processed_dir)
            input_paths.extend(sorted(path for path in processed_path.glob('*.csv')
                                      if path.is_file() and path.name.startswith(('source_', 'gtfs_'))))
            meta_path = processed_path.parent / 'source_metadata.json'
            if meta_path.exists():
                source_metadata = json.loads(meta_path.read_text(encoding='utf-8'))
                input_paths.append(meta_path)
            stats_path = processed_path.parent / 'gtfs_atlas_stats.json'
            if stats_path.exists() and 'gtfs_atlas_statistics' not in source_metadata:
                source_metadata['gtfs_atlas_statistics'] = json.loads(stats_path.read_text(encoding='utf-8'))
                input_paths.append(stats_path)
    gtfs_stats = source_metadata.get('gtfs_atlas_statistics', {})
    if gtfs_dir is not None:
        with progress_stage('timetable.local_feed'):
            source_products, stop_rows, identity_rows, gtfs_stats = _fresh_gtfs_products(gtfs_dir, traffic_points, boundary_geojson)
            route_data.update(source_products)
    with progress_stage('osm.prepare', timing_name='osm.prepare'):
        with stage('osm.route_products'):
            route_data.update(process_osm_routes_data(osm_path.read_text(encoding='utf-8'), out_dir=None))
        for key in ('osm_route_masters', 'osm_route_relations'):
            if key in route_data and 'gtfs_route_id' in route_data[key]:
                route_data[key]['route_id_normalized'] = route_data[key]['gtfs_route_id'].map(normalize_route_id)
        source = atlas_state(records, route_data)
        with stage('osm.stops_and_evidence'):
            osm = read_osm(osm_path, route_id_normalizer=normalize_route_id)
        route_state = RouteState.from_records(
            [{'route_id': row['route_id'], 'route_id_normalized': row.get('route_id_normalized')}
             for row in route_data.get('source_routes', pd.DataFrame()).to_dict('records')],
            route_data.get('osm_route_relations', pd.DataFrame()).to_dict('records'),
        )
    with stage('matching.stops_and_problems'):
        output = match(source, osm, SWITZERLAND, route_evidence={'route_state': route_state})
    output.diagnostics.extend(unresolved_osm_member_diagnostics(route_data))
    keys_by_id = {stop.source_id: stop.key for stop in records}
    with stage('matching.routes'):
        output.routes = compare_routes(
            scope_source_stop_keys(route_data, keys_by_id), set(keys_by_id.values()), base_data=output,
            require_direction_match=SWITZERLAND.itinerary_require_direction,
            min_stop_ratio=SWITZERLAND.itinerary_min_stop_ratio,
            line_number_alias=timetable_line_number,
        )
    output.extensions.update({'gtfs_stops': stop_rows, 'gtfs_atlas_state': identity_rows,
                              'gtfs_atlas_stats': gtfs_stats, 'atlas_filtering': source_metadata.get('atlas_filtering', {})})
    if gtfs_dir is not None:
        input_paths.extend(Path(gtfs_dir) / name for name in ('stops.txt', 'routes.txt', 'trips.txt', 'stop_times.txt'))
    if boundary_geojson is not None:
        input_paths.append(Path(boundary_geojson))
    capabilities = [cap for cap in output.metadata.get('capabilities', []) if cap != 'routes']
    if output.routes.get('line_families'):
        capabilities.append('routes')
    if stop_rows and 'gtfs_identity' not in capabilities:
        capabilities.append('gtfs_identity')
    output.metadata['capabilities'] = capabilities
    with progress_stage('bundle.fingerprints', timing_name='matching.fingerprints'):
        output.metadata.update({
            'source': 'ATLAS', 'namespace': 'atlas',
            'inputs': [fingerprint(path) for path in input_paths],
            'attribution': {'source': 'opentransportdata.swiss', 'osm': '© OpenStreetMap contributors, ODbL'},
            'gtfs_identity_statistics': gtfs_stats,
            'gtfs_geography_filter': 'explicit_boundary' if boundary_geojson else 'preselected_input',
            # Input problems that weakened this run without stopping it.
            'warnings': list(source_metadata.get('atlas_filtering', {}).get('warnings', [])),
        })
    return output
