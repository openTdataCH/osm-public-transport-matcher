"""Route and ordered itinerary comparison over explicit normalized input records.

This module has no file access or persistence dependencies. Adapters supply the
source products ``source_routes``, ``source_itineraries`` and
``source_stop_calls`` and the OSM route products; ``compare_routes`` returns the
bundle's ``routes`` section.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from itertools import count
from typing import Any

import pandas as pd

from public_transport_matcher.core.models import MatchingOutput
from public_transport_matcher.progress import progress_stage


_ITINERARY_MATCH_MIN_RATIO = 0.8


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    text = str(value).strip()
    return text == '' or text.lower() == 'nan'


def _to_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    return str(value).strip()


def _to_int(value: Any, default: int = 0) -> int:
    if _is_missing(value):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hash_sequence(values: list[str]) -> str:
    digest = hashlib.sha1('>'.join(values).encode('utf-8')).hexdigest()
    return digest[:16]


def _records(dataframe: pd.DataFrame | None) -> list[dict[str, Any]]:
    if dataframe is None or dataframe.empty:
        return []
    return dataframe.to_dict(orient='records')


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        text = _to_text(value)
        if text is not None:
            return text
    return None


def _build_base_lookups(base_data: MatchingOutput | None, known_source_keys: set[str]) -> dict[str, Any]:
    source_stop_lookup: dict[str, dict[str, Any]] = {}
    source_key_candidates_by_station: dict[str, set[str]] = defaultdict(set)
    osm_node_lookup: dict[str, Any] = {}
    matched_osm_to_source_key: dict[str, str] = {}

    if base_data is None:
        return {
            'source_stop_lookup': source_stop_lookup,
            'osm_node_lookup': osm_node_lookup,
            'matched_osm_to_source_key': matched_osm_to_source_key,
            'unique_source_key_by_station_ref': {},
        }

    def register_source_stop(node: Any) -> None:
        key = _to_text(getattr(node, 'key', None))
        if key is None:
            return
        station_ref = _to_text(getattr(node, 'station_ref', None))
        source_stop_lookup[key] = {
            'lat': _to_float(getattr(node, 'lat', None)),
            'lon': _to_float(getattr(node, 'lon', None)),
            'station_ref': station_ref,
            'platform_code': _to_text(getattr(node, 'platform_code', None)),
            'name': _to_text(getattr(node, 'name', None)),
        }
        if key in known_source_keys and station_ref is not None:
            source_key_candidates_by_station[station_ref].add(key)

    def register_osm_node(node: Any) -> None:
        node_id = _to_text(getattr(node, 'node_id', None))
        if node_id is None:
            return
        osm_node_lookup[node_id] = node

    for match in getattr(base_data, 'matched', []):
        register_source_stop(match.source_node)
        register_osm_node(match.osm_node)
        source_key = _to_text(match.source_node.key)
        osm_node_id = _to_text(match.osm_node.node_id)
        if source_key in known_source_keys and osm_node_id is not None:
            matched_osm_to_source_key[osm_node_id] = source_key

    for source_node in getattr(base_data, 'unmatched_source', []):
        register_source_stop(source_node)
    for osm_node in getattr(base_data, 'unmatched_osm', []):
        register_osm_node(osm_node)
    for osm_node in getattr(base_data, 'all_osm_nodes', []):
        register_osm_node(osm_node)
    # Route relations can still list stops that OSM marks as disused.
    for osm_node in getattr(base_data, 'disused_osm', []):
        register_osm_node(osm_node)

    unique_source_key_by_station_ref = {
        station_ref: next(iter(keys))
        for station_ref, keys in source_key_candidates_by_station.items()
        if len(keys) == 1
    }
    return {
        'source_stop_lookup': source_stop_lookup,
        'osm_node_lookup': osm_node_lookup,
        'matched_osm_to_source_key': matched_osm_to_source_key,
        'unique_source_key_by_station_ref': unique_source_key_by_station_ref,
    }


def _build_source_stop_call_row(
    row: dict[str, Any],
    known_source_keys: set[str],
    source_stop_lookup: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """Normalize one source stop call; report whether its stop key was unknown."""
    source_stop_key = _to_text(row.get('source_stop_key'))
    unknown_reference = False
    if source_stop_key not in known_source_keys:
        unknown_reference = source_stop_key is not None
        source_stop_key = None

    stop = source_stop_lookup.get(source_stop_key) if source_stop_key else None
    source_stop_id = _to_text(row.get('source_stop_id'))
    stop_lat = _to_float(row.get('stop_lat'))
    stop_lon = _to_float(row.get('stop_lon'))
    if stop is not None:
        stop_lat = stop_lat if stop_lat is not None else stop.get('lat')
        stop_lon = stop_lon if stop_lon is not None else stop.get('lon')

    variants = [
        key for key in _parse_json_list(row.get('source_stop_key_variants'))
        if key in known_source_keys
    ]
    return {
        'itinerary_id': _to_text(row.get('itinerary_id')),
        'stop_sequence': _to_int(row.get('stop_sequence')),
        'source_stop_id': source_stop_id,
        'source_stop_key': source_stop_key,
        'source_stop_key_variants': json.dumps(variants) if variants else None,
        'canonical_stop_key': _first_non_empty(row.get('canonical_stop_key'), source_stop_key, source_stop_id),
        'stop_label': _first_non_empty(
            row.get('stop_label'),
            stop.get('name') if stop else None,
            stop.get('platform_code') if stop else None,
            source_stop_id,
        ),
        'station_ref': _first_non_empty(row.get('station_ref'), stop.get('station_ref') if stop else None),
        'platform_code': _to_text(row.get('platform_code')),
        'stop_lat': stop_lat,
        'stop_lon': stop_lon,
    }, unknown_reference


def _build_source_rows(
    route_data: dict[str, pd.DataFrame],
    known_source_keys: set[str],
    source_stop_lookup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    route_rows: list[dict[str, Any]] = []
    for row in _records(route_data.get('source_routes')):
        route_id = _to_text(row.get('route_id'))
        if route_id is None:
            continue
        route_rows.append({
            'route_id': route_id,
            'route_id_normalized': _first_non_empty(row.get('route_id_normalized'), route_id),
            'agency_id': _to_text(row.get('agency_id')),
            'route_short_name': _to_text(row.get('route_short_name')),
            'route_long_name': _to_text(row.get('route_long_name')),
            'route_desc': _to_text(row.get('route_desc')),
            'route_type': _to_text(row.get('route_type')),
        })

    itinerary_rows: list[dict[str, Any]] = []
    stop_call_rows: list[dict[str, Any]] = []
    unknown_references = 0
    itineraries = route_data.get('source_itineraries')
    stop_calls = route_data.get('source_stop_calls')
    if itineraries is not None and stop_calls is not None:
        for row in _records(itineraries):
            itinerary_id = _to_text(row.get('itinerary_id'))
            route_id = _to_text(row.get('route_id'))
            if itinerary_id is None or route_id is None:
                continue
            itinerary_rows.append({
                'itinerary_id': itinerary_id,
                'route_id': route_id,
                'direction_id': _to_text(row.get('direction_id')),
                'representative_headsign': _to_text(row.get('representative_headsign')),
                'direction_label': _to_text(row.get('direction_label')),
                'trip_count': _to_int(row.get('trip_count'), default=0),
                'shape_id': _to_text(row.get('shape_id')),
                'geometry_wkt': _to_text(row.get('geometry_wkt')),
                'headsign_or_pattern_hash': _to_text(row.get('headsign_or_pattern_hash')),
            })
        for row in _records(stop_calls):
            normalized_row, unknown = _build_source_stop_call_row(row, known_source_keys, source_stop_lookup)
            if normalized_row['itinerary_id'] is None:
                continue
            unknown_references += 1 if unknown else 0
            stop_call_rows.append(normalized_row)

    return {
        'routes': route_rows,
        'itineraries': itinerary_rows,
        'stop_calls': stop_call_rows,
        'unknown_stop_references': unknown_references,
    }


def _resolve_osm_stop_fields(node_id: str | None, row: dict[str, Any], lookups: dict[str, Any]) -> dict[str, Any]:
    osm_node_lookup = lookups['osm_node_lookup']
    source_stop_lookup = lookups['source_stop_lookup']

    node = osm_node_lookup.get(node_id) if node_id is not None else None
    station_ref = _first_non_empty(row.get('uic_ref'), getattr(node, 'station_ref', None) if node else None)
    canonical_stop_key = lookups['matched_osm_to_source_key'].get(node_id) if node_id is not None else None
    if canonical_stop_key is None and station_ref is not None:
        canonical_stop_key = lookups['unique_source_key_by_station_ref'].get(station_ref)
    if canonical_stop_key is None:
        canonical_stop_key = _to_text(row.get('canonical_stop_key'))
    if canonical_stop_key is None and node_id is not None:
        canonical_stop_key = f'osm:{node_id}'

    source_stop = source_stop_lookup.get(canonical_stop_key)
    stop_lat = _to_float(row.get('stop_lat'))
    stop_lon = _to_float(row.get('stop_lon'))
    if stop_lat is None and node is not None:
        stop_lat = _to_float(getattr(node, 'lat', None))
    if stop_lon is None and node is not None:
        stop_lon = _to_float(getattr(node, 'lon', None))
    if source_stop is not None:
        stop_lat = stop_lat if stop_lat is not None else source_stop.get('lat')
        stop_lon = stop_lon if stop_lon is not None else source_stop.get('lon')

    return {
        'canonical_stop_key': canonical_stop_key,
        'resolved_source_key': canonical_stop_key if source_stop is not None else None,
        'stop_label': _first_non_empty(
            row.get('stop_label'),
            getattr(node, 'name', None) if node else None,
            getattr(node, 'uic_name', None) if node else None,
            getattr(node, 'local_ref', None) if node else None,
            node_id,
        ),
        'station_ref': station_ref,
        'stop_lat': stop_lat,
        'stop_lon': stop_lon,
    }


def _build_osm_rows(route_data: dict[str, pd.DataFrame], lookups: dict[str, Any]) -> dict[str, Any]:
    route_master_rows: list[dict[str, Any]] = []
    for row in _records(route_data.get('osm_route_masters')):
        route_master_id = _to_text(row.get('route_master_id'))
        if route_master_id is None:
            continue
        route_master_rows.append({
            'route_master_id': route_master_id,
            'route_master': _to_text(row.get('route_master')),
            'name': _to_text(row.get('name')),
            'ref': _to_text(row.get('ref')),
            'operator': _to_text(row.get('operator')),
            'operator_wikidata': _to_text(row.get('operator_wikidata')),
            'network': _to_text(row.get('network')),
            'network_wikidata': _to_text(row.get('network_wikidata')),
            'is_non_gtfs': str(row.get('is_non_gtfs')).lower() == 'true',
            'colour': _to_text(row.get('colour')),
            'gtfs_route_id': _to_text(row.get('gtfs_route_id')),
            'route_id_normalized': _to_text(row.get('route_id_normalized')),
            'run_id': _to_text(row.get('run_id')),
        })

    relation_rows: list[dict[str, Any]] = []
    for row in _records(route_data.get('osm_route_relations')):
        relation_id = _to_text(row.get('relation_id'))
        if relation_id is None:
            continue
        relation_rows.append({
            'relation_id': relation_id,
            'route': _to_text(row.get('route')),
            'name': _to_text(row.get('name')),
            'ref': _to_text(row.get('ref')),
            'operator': _to_text(row.get('operator')),
            'operator_wikidata': _to_text(row.get('operator_wikidata')),
            'network': _to_text(row.get('network')),
            'network_wikidata': _to_text(row.get('network_wikidata')),
            'is_non_gtfs': str(row.get('is_non_gtfs')).lower() == 'true',
            'from_name': _to_text(row.get('from_name')),
            'to_name': _to_text(row.get('to_name')),
            'via': _to_text(row.get('via')),
            'public_transport_version': _to_text(row.get('public_transport_version')),
            'colour': _to_text(row.get('colour')),
            'gtfs_route_id': _to_text(row.get('gtfs_route_id')),
            'route_id_normalized': _to_text(row.get('route_id_normalized')),
            'gtfs_trip_id': _to_text(row.get('gtfs_trip_id')),
            'gtfs_trip_id_sample': _to_text(row.get('gtfs_trip_id_sample')),
            'gtfs_shape_id': _to_text(row.get('gtfs_shape_id')),
            'route_master_id': _to_text(row.get('route_master_id')),
            'family_origin': _to_text(row.get('family_origin')),
            'synthetic_family_key': _to_text(row.get('synthetic_family_key')),
            'run_id': _to_text(row.get('run_id')),
        })

    relation_member_rows: list[dict[str, Any]] = []
    relation_member_source = route_data.get('osm_route_relation_members')
    explicit_relation_stops = route_data.get('osm_route_relation_stops')
    if explicit_relation_stops is not None and not explicit_relation_stops.empty:
        relation_member_source = None
    elif relation_member_source is not None and 'resolved_node_id' in relation_member_source:
        resolved = relation_member_source['resolved_node_id']
        relation_member_source = relation_member_source.loc[resolved.notna() & resolved.ne('')]
    for row in _records(relation_member_source):
        relation_id = _to_text(row.get('relation_id'))
        member_type = _first_non_empty(row.get('member_type'), 'node' if _to_text(row.get('resolved_node_id')) else None)
        member_ref = _first_non_empty(row.get('member_ref'), row.get('resolved_node_id'))
        if relation_id is None or member_type is None or member_ref is None:
            continue
        relation_member_rows.append({
            'relation_id': relation_id,
            'member_type': member_type,
            'member_ref': member_ref,
            'member_role': _to_text(row.get('member_role')),
            'member_sequence': _to_int(row.get('member_sequence')),
            'resolved_node_id': _to_text(row.get('resolved_node_id')),
            'direction_id_derived': _to_text(row.get('direction_id_derived')),
        })

    relation_stop_rows: list[dict[str, Any]] = []
    if explicit_relation_stops is not None and not explicit_relation_stops.empty:
        for row in _records(explicit_relation_stops):
            relation_id = _to_text(row.get('relation_id'))
            osm_node_id = _to_text(row.get('osm_node_id'))
            if relation_id is None or osm_node_id is None:
                continue
            resolved = _resolve_osm_stop_fields(osm_node_id, row, lookups)
            relation_stop_rows.append({
                'relation_id': relation_id,
                'direction_id': _to_text(row.get('direction_id')),
                'stop_sequence': _to_int(row.get('stop_sequence')),
                'osm_node_id': osm_node_id,
                'stop_role': _to_text(row.get('stop_role')),
                **resolved,
            })
    else:
        for row in relation_member_rows:
            osm_node_id = _to_text(row.get('resolved_node_id'))
            if osm_node_id is None:
                continue
            resolved = _resolve_osm_stop_fields(osm_node_id, row, lookups)
            relation_stop_rows.append({
                'relation_id': row['relation_id'],
                'direction_id': row.get('direction_id_derived'),
                'stop_sequence': row['member_sequence'],
                'osm_node_id': osm_node_id,
                'stop_role': row.get('member_role'),
                **resolved,
            })

    relation_stop_rows.sort(key=lambda row: (row['relation_id'], row['stop_sequence'], row['osm_node_id'] or ''))
    return {
        'osm_route_masters': route_master_rows,
        'osm_route_relations': relation_rows,
        'osm_route_relation_stops': relation_stop_rows,
    }


def _build_line_family_rows(
    source_rows: dict[str, Any],
    osm_rows: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, int]]:
    line_family_id_counter = count(1)
    line_family_rows: list[dict[str, Any]] = []
    route_id_to_family_id: dict[str, int] = {}
    relation_id_to_family_id: dict[str, int] = {}

    for row in source_rows['routes']:
        family_id = next(line_family_id_counter)
        route_id = row['route_id']
        route_id_to_family_id[route_id] = family_id
        line_family_rows.append({
            'id': family_id,
            'side': 'source',
            'key': route_id,
            'family_origin': 'source_route',
            'route_type': row.get('route_type'),
            'display_route_id': route_id,
            'public_name': _first_non_empty(row.get('route_long_name'), row.get('route_short_name'), route_id),
            'ref': row.get('route_short_name'),
            'operator': row.get('agency_id'),
            'operator_wikidata': None,
            'network': None,
            'network_wikidata': None,
            'is_non_gtfs': False,
            'gtfs_route_id': route_id,
            'normalized_route_id': _first_non_empty(row.get('route_id_normalized'), route_id),
            'route_master_id': None,
            'representative_relation_id': None,
        })

    route_master_lookup = {row['route_master_id']: row for row in osm_rows['osm_route_masters']}
    family_groups: dict[tuple[str, str], dict[str, Any]] = {}

    for row in osm_rows['osm_route_masters']:
        key = ('route_master', row['route_master_id'])
        family_groups[key] = {
            'family_origin': 'route_master',
            'key': row['route_master_id'],
            'route_master_row': row,
            'relations': [],
        }

    for relation in osm_rows['osm_route_relations']:
        route_master_id = relation.get('route_master_id')
        gtfs_route_id = relation.get('gtfs_route_id')
        normalized_gtfs_route_id = _first_non_empty(relation.get('route_id_normalized'), gtfs_route_id)
        ref = relation.get('ref')
        operator = relation.get('operator')
        network = relation.get('network')
        route = relation.get('route')

        if route_master_id and route_master_id in route_master_lookup:
            key = ('route_master', route_master_id)
            family_origin = 'route_master'
            family_key = route_master_id
        elif normalized_gtfs_route_id:
            key = ('synthetic_gtfs_route_id', normalized_gtfs_route_id)
            family_origin = 'synthetic_gtfs_route_id'
            family_key = normalized_gtfs_route_id
        elif _first_non_empty(ref, operator, network, route):
            synthetic_key = '|'.join(value or '' for value in (route, ref, operator, network))
            key = ('synthetic_ref_operator', synthetic_key)
            family_origin = 'synthetic_ref_operator'
            family_key = synthetic_key
        else:
            key = ('synthetic_relation', relation['relation_id'])
            family_origin = 'synthetic_relation'
            family_key = relation['relation_id']

        group = family_groups.setdefault(
            key,
            {
                'family_origin': family_origin,
                'key': family_key,
                'route_master_row': route_master_lookup.get(route_master_id),
                'relations': [],
            },
        )
        group['relations'].append(relation)

    for group in family_groups.values():
        family_id = next(line_family_id_counter)
        route_master_row = group.get('route_master_row') or {}
        relations = group.get('relations') or []
        representative_relation = relations[0] if relations else {}
        gtfs_route_id = _first_non_empty(route_master_row.get('gtfs_route_id'), representative_relation.get('gtfs_route_id'))
        normalized_route_id = _first_non_empty(route_master_row.get('route_id_normalized'), representative_relation.get('route_id_normalized'), gtfs_route_id, representative_relation.get('ref'))
        line_family_rows.append({
            'id': family_id,
            'side': 'osm',
            'key': group['key'],
            'family_origin': group['family_origin'],
            'route_type': _first_non_empty(representative_relation.get('route'), route_master_row.get('route_master')),
            'display_route_id': _first_non_empty(gtfs_route_id, route_master_row.get('ref'), representative_relation.get('ref'), group['key']),
            'public_name': _first_non_empty(route_master_row.get('name'), representative_relation.get('name'), representative_relation.get('ref'), group['key']),
            'ref': _first_non_empty(route_master_row.get('ref'), representative_relation.get('ref')),
            'operator': _first_non_empty(route_master_row.get('operator'), representative_relation.get('operator')),
            'operator_wikidata': _first_non_empty(route_master_row.get('operator_wikidata'), representative_relation.get('operator_wikidata')),
            'network': _first_non_empty(route_master_row.get('network'), representative_relation.get('network')),
            'network_wikidata': _first_non_empty(route_master_row.get('network_wikidata'), representative_relation.get('network_wikidata')),
            'is_non_gtfs': bool(route_master_row.get('is_non_gtfs') or representative_relation.get('is_non_gtfs')),
            'gtfs_route_id': gtfs_route_id,
            'normalized_route_id': normalized_route_id,
            'route_master_id': route_master_row.get('route_master_id'),
            'representative_relation_id': representative_relation.get('relation_id'),
        })
        for relation in relations:
            relation_id_to_family_id[relation['relation_id']] = family_id

    return line_family_rows, route_id_to_family_id, relation_id_to_family_id


def _build_itinerary_rows(
    source_rows: dict[str, Any],
    osm_rows: dict[str, Any],
    route_id_to_family_id: dict[str, int],
    relation_id_to_family_id: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    itinerary_id_counter = count(1)
    stop_call_id_counter = count(1)
    itinerary_rows: list[dict[str, Any]] = []
    stop_call_rows: list[dict[str, Any]] = []
    stop_calls_by_itinerary_id: dict[int, list[dict[str, Any]]] = defaultdict(list)

    source_calls_by_itinerary: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows['stop_calls']:
        source_calls_by_itinerary[row['itinerary_id']].append(row)
    for rows in source_calls_by_itinerary.values():
        rows.sort(key=lambda row: row['stop_sequence'])

    for row in source_rows['itineraries']:
        line_family_id = route_id_to_family_id.get(row['route_id'])
        if line_family_id is None:
            continue
        itinerary_id = next(itinerary_id_counter)
        call_rows = source_calls_by_itinerary.get(row['itinerary_id'], [])
        itinerary_rows.append({
            'id': itinerary_id,
            'side': 'source',
            'key': row['itinerary_id'],
            'line_family_id': line_family_id,
            'direction_id': row.get('direction_id'),
            'headsign_or_pattern_hash': row.get('headsign_or_pattern_hash'),
            'display_name': _first_non_empty(row.get('direction_label'), row.get('representative_headsign'), row['itinerary_id']),
            'representative_headsign': row.get('representative_headsign'),
            'from_name': None,
            'to_name': None,
            'trip_count': row.get('trip_count'),
            'shape_id': row.get('shape_id'),
            'geometry_wkt': row.get('geometry_wkt'),
            'canonical_stop_count': len(call_rows),
        })
        for call_row in call_rows:
            stop_call = {
                'id': next(stop_call_id_counter),
                'itinerary_id': itinerary_id,
                'stop_sequence': call_row['stop_sequence'],
                'source_stop_id': call_row.get('source_stop_id'),
                'source_stop_key': call_row.get('source_stop_key'),
                'source_stop_key_variants': call_row.get('source_stop_key_variants'),
                'osm_node_id': None,
                'canonical_stop_key': call_row.get('canonical_stop_key'),
                'stop_label': call_row.get('stop_label'),
                'station_ref': call_row.get('station_ref'),
                'platform_code': call_row.get('platform_code'),
                'stop_lat': call_row.get('stop_lat'),
                'stop_lon': call_row.get('stop_lon'),
                'member_role': None,
            }
            stop_call_rows.append(stop_call)
            stop_calls_by_itinerary_id[itinerary_id].append(stop_call)

    relation_stop_rows_by_relation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in osm_rows['osm_route_relation_stops']:
        relation_stop_rows_by_relation[row['relation_id']].append(row)
    for rows in relation_stop_rows_by_relation.values():
        rows.sort(key=lambda row: row['stop_sequence'])

    for row in osm_rows['osm_route_relations']:
        line_family_id = relation_id_to_family_id.get(row['relation_id'])
        if line_family_id is None:
            continue
        call_rows = relation_stop_rows_by_relation.get(row['relation_id'], [])
        itinerary_id = next(itinerary_id_counter)
        sequence_pattern_hash = _hash_sequence([
            _first_non_empty(call_row.get('canonical_stop_key'), f"osm:{call_row.get('osm_node_id')}")
            for call_row in call_rows
        ]) if call_rows else None
        direction_id = next((call_row.get('direction_id') for call_row in call_rows if call_row.get('direction_id') is not None), None)
        itinerary_rows.append({
            'id': itinerary_id,
            'side': 'osm',
            'key': row['relation_id'],
            'line_family_id': line_family_id,
            'direction_id': direction_id,
            'headsign_or_pattern_hash': sequence_pattern_hash,
            'display_name': _first_non_empty(
                f"{row.get('from_name')} -> {row.get('to_name')}" if row.get('from_name') or row.get('to_name') else None,
                row.get('name'),
                row.get('ref'),
                row['relation_id'],
            ),
            'representative_headsign': _first_non_empty(row.get('to_name'), row.get('name'), row.get('ref')),
            'from_name': row.get('from_name'),
            'to_name': row.get('to_name'),
            'trip_count': 1,
            'shape_id': row.get('gtfs_shape_id'),
            'geometry_wkt': None,
            'canonical_stop_count': len(call_rows),
        })
        for call_row in call_rows:
            stop_call = {
                'id': next(stop_call_id_counter),
                'itinerary_id': itinerary_id,
                'stop_sequence': call_row['stop_sequence'],
                'source_stop_id': None,
                'source_stop_key': call_row.get('resolved_source_key'),
                'source_stop_key_variants': None,
                'osm_node_id': call_row.get('osm_node_id'),
                'canonical_stop_key': call_row.get('canonical_stop_key'),
                'stop_label': call_row.get('stop_label'),
                'station_ref': call_row.get('station_ref'),
                'platform_code': None,
                'stop_lat': call_row.get('stop_lat'),
                'stop_lon': call_row.get('stop_lon'),
                'member_role': call_row.get('stop_role'),
            }
            stop_call_rows.append(stop_call)
            stop_calls_by_itinerary_id[itinerary_id].append(stop_call)

    return itinerary_rows, stop_call_rows, stop_calls_by_itinerary_id


def _candidate_line_family_pairs(source_families, osm_families):
    indexes = {key: defaultdict(list) for key in ('gtfs_route_id', 'normalized_route_id', 'display_route_id')}
    for osm in osm_families:
        if not osm.get('is_non_gtfs'):
            for key, index in indexes.items():
                value = _to_text(osm.get(key))
                if value:
                    index[value].append(osm)
    candidates = []
    for source in source_families:
        possible = {}
        for key, value in (('gtfs_route_id', source.get('gtfs_route_id')),
                           ('normalized_route_id', source.get('normalized_route_id')),
                           ('display_route_id', source.get('gtfs_route_id'))):
            for osm in indexes[key].get(_to_text(value), ()):
                possible[osm['id']] = osm
        for osm in possible.values():
            score, reason = _score_line_family_pair(source, osm)
            if score > 0 and reason:
                candidates.append((score, source, osm, reason))
    return candidates


def _score_line_family_pair(source_family: dict[str, Any], osm_family: dict[str, Any]) -> tuple[float, str | None]:
    if source_family['side'] != 'source' or osm_family['side'] != 'osm':
        return 0.0, None

    source_route_id = _to_text(source_family.get('gtfs_route_id'))
    source_normalized = _to_text(source_family.get('normalized_route_id'))

    osm_gtfs_route_id = _to_text(osm_family.get('gtfs_route_id'))
    osm_normalized = _to_text(osm_family.get('normalized_route_id'))

    if source_route_id and osm_gtfs_route_id and source_route_id == osm_gtfs_route_id:
        return 1.0, 'exact_gtfs_route_id'
    if source_normalized and osm_normalized and source_normalized == osm_normalized:
        return 0.95, 'normalized_gtfs_route_id'
    if source_route_id and _to_text(osm_family.get('display_route_id')) == source_route_id:
        return 0.9, 'display_route_id_match'
    return 0.0, None


def _parse_json_list(value: Any) -> list[str]:
    text = _to_text(value)
    if text is None:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in (_to_text(entry) for entry in parsed) if item is not None]


def _stop_identity(stop_call: dict[str, Any]) -> tuple[frozenset[str], str | None]:
    """Resolved source keys and the station reference of one stop call.

    Canonical fallback keys can be opaque, unresolved source IDs, so only
    explicit resolved source keys establish physical stop identity.
    """
    identities = set(_parse_json_list(stop_call.get('source_stop_key_variants')))
    source_stop_key = _to_text(stop_call.get('source_stop_key'))
    if source_stop_key:
        identities.add(source_stop_key)
    return frozenset(identities), _to_text(stop_call.get('station_ref'))


def _same_stop(left, right) -> bool:
    left_keys, left_station = left
    right_keys, right_station = right
    if left_keys and right_keys:
        return bool(left_keys & right_keys)
    return bool(left_station and left_station == right_station)


def _count_stop_sequence_matches(source_calls, osm_calls):
    """Length of the longest common subsequence of stops, in O(m) memory."""
    source = [_stop_identity(call) for call in source_calls]
    osm = [_stop_identity(call) for call in osm_calls]
    previous = [0] * (len(osm) + 1)
    for left_call in source:
        current = [0]
        for j, right_call in enumerate(osm):
            current.append(previous[j] + 1 if _same_stop(left_call, right_call)
                           else max(previous[j + 1], current[j]))
        previous = current
    return previous[-1]


def _direction_ids_match(source_itinerary: dict[str, Any], osm_itinerary: dict[str, Any]) -> bool:
    source_direction = _to_text(source_itinerary.get('direction_id'))
    osm_direction = _to_text(osm_itinerary.get('direction_id'))
    return source_direction is not None and source_direction == osm_direction


def _score_itinerary_pair(
    source_itinerary: dict[str, Any],
    osm_itinerary: dict[str, Any],
    source_calls: list[dict[str, Any]],
    osm_calls: list[dict[str, Any]],
    *,
    require_direction_match: bool = True,
    min_stop_ratio: float = _ITINERARY_MATCH_MIN_RATIO,
) -> dict[str, Any]:
    direction_matches = _direction_ids_match(source_itinerary, osm_itinerary)
    matched_stop_count = _count_stop_sequence_matches(source_calls, osm_calls)
    stop_score = round(matched_stop_count / max(len(source_calls), len(osm_calls), 1), 4)
    directions_known = _to_text(source_itinerary.get('direction_id')) is not None and _to_text(osm_itinerary.get('direction_id')) is not None
    direction_eligible = direction_matches or (not require_direction_match and not directions_known)
    is_eligible = direction_eligible and stop_score >= min_stop_ratio
    if not direction_eligible:
        match_reason = 'direction_mismatch'
    elif is_eligible:
        match_reason = 'ordered_stop_match' if direction_matches else 'ordered_stop_match_direction_unknown'
    else:
        match_reason = 'below_stop_match_threshold'
    return {
        'direction_score': 1.0 if direction_matches else 0.0,
        'stop_score': stop_score,
        'geometry_score': None,
        'overall_score': stop_score,
        'match_reason': match_reason,
        'matched_stop_count': matched_stop_count,
        'source_stop_count': len(source_calls),
        'osm_stop_count': len(osm_calls),
        'is_eligible': is_eligible,
    }


def _choose_best_itinerary_pairs(
    source_itineraries: list[dict[str, Any]],
    osm_itineraries: list[dict[str, Any]],
    pair_scores: dict[tuple[int, int], dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Choose eligible pairs greedily and one-to-one, most shared stops first."""
    candidates = [
        (source_itinerary, osm_itinerary, pair_scores[(source_itinerary['id'], osm_itinerary['id'])])
        for source_itinerary in sorted(source_itineraries, key=lambda row: row['id'])
        for osm_itinerary in sorted(osm_itineraries, key=lambda row: row['id'])
        if pair_scores[(source_itinerary['id'], osm_itinerary['id'])]['is_eligible']
    ]
    candidates.sort(
        key=lambda item: (
            -item[2]['matched_stop_count'],
            -item[2]['stop_score'],
            item[0]['id'],
            item[1]['id'],
        )
    )

    chosen_pairs: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    used_source_ids: set[int] = set()
    used_osm_ids: set[int] = set()
    for source_itinerary, osm_itinerary, score_row in candidates:
        if source_itinerary['id'] in used_source_ids or osm_itinerary['id'] in used_osm_ids:
            continue
        used_source_ids.add(source_itinerary['id'])
        used_osm_ids.add(osm_itinerary['id'])
        chosen_pairs.append((source_itinerary, osm_itinerary, score_row))

    return chosen_pairs


def compare_routes(
    route_data: dict[str, pd.DataFrame],
    known_source_keys: set[str],
    base_data: MatchingOutput | None = None,
    *,
    require_direction_match: bool = True,
    min_stop_ratio: float = _ITINERARY_MATCH_MIN_RATIO,
) -> dict[str, Any]:
    """Compare route families and ordered itineraries, returning the bundle's routes section."""
    if not 0 < min_stop_ratio <= 1:
        raise ValueError('min_stop_ratio must be greater than zero and at most one')
    with progress_stage('route_matching.normalize'):
        lookups = _build_base_lookups(base_data, known_source_keys)
        source_rows = _build_source_rows(route_data, known_source_keys, lookups['source_stop_lookup'])
        osm_rows = _build_osm_rows(route_data, lookups)

        line_family_rows, route_id_to_family_id, relation_id_to_family_id = _build_line_family_rows(source_rows, osm_rows)
        itinerary_rows, stop_call_rows, stop_calls_by_itinerary_id = _build_itinerary_rows(
            source_rows,
            osm_rows,
            route_id_to_family_id,
            relation_id_to_family_id,
        )

    source_line_families = [row for row in line_family_rows if row['side'] == 'source']
    osm_line_families = [row for row in line_family_rows if row['side'] == 'osm']
    line_family_match_id_counter = count(1)
    itinerary_match_id_counter = count(1)

    line_family_match_rows: list[dict[str, Any]] = []
    itinerary_match_rows: list[dict[str, Any]] = []

    with progress_stage('route_matching.families'):
        candidate_pairs = _candidate_line_family_pairs(source_line_families, osm_line_families)
        candidate_pairs.sort(key=lambda item: (-item[0], item[1]['id'], item[2]['id']))

        matched_source_family_ids: set[int] = set()
        matched_osm_family_ids: set[int] = set()
        for score, source_family, osm_family, reason in candidate_pairs:
            if source_family['id'] in matched_source_family_ids or osm_family['id'] in matched_osm_family_ids:
                continue
            line_family_match_rows.append({
                'id': next(line_family_match_id_counter),
                'source_line_family_id': source_family['id'],
                'osm_line_family_id': osm_family['id'],
                'match_method': reason,
            })
            matched_source_family_ids.add(source_family['id'])
            matched_osm_family_ids.add(osm_family['id'])

    itineraries_by_family_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for itinerary_row in itinerary_rows:
        itineraries_by_family_id[itinerary_row['line_family_id']].append(itinerary_row)
    for itinerary_list in itineraries_by_family_id.values():
        itinerary_list.sort(key=lambda row: row['id'])

    with progress_stage('route_matching.itineraries'):
        for line_family_match_row in line_family_match_rows:
            source_itineraries = itineraries_by_family_id.get(line_family_match_row['source_line_family_id'], [])
            osm_itineraries = itineraries_by_family_id.get(line_family_match_row['osm_line_family_id'], [])
            pair_scores: dict[tuple[int, int], dict[str, Any]] = {}
            for source_itinerary in source_itineraries:
                source_calls = stop_calls_by_itinerary_id.get(source_itinerary['id'], [])
                for osm_itinerary in osm_itineraries:
                    osm_calls = stop_calls_by_itinerary_id.get(osm_itinerary['id'], [])
                    pair_scores[(source_itinerary['id'], osm_itinerary['id'])] = _score_itinerary_pair(
                        source_itinerary,
                        osm_itinerary,
                        source_calls,
                        osm_calls,
                        require_direction_match=require_direction_match,
                        min_stop_ratio=min_stop_ratio,
                    )

            for source_itinerary, osm_itinerary, score_row in _choose_best_itinerary_pairs(source_itineraries, osm_itineraries, pair_scores):
                itinerary_match_rows.append({
                    'id': next(itinerary_match_id_counter),
                    'line_family_match_id': line_family_match_row['id'],
                    'source_itinerary_id': source_itinerary['id'],
                    'osm_itinerary_id': osm_itinerary['id'],
                    'direction_score': score_row['direction_score'],
                    'stop_score': score_row['stop_score'],
                    'geometry_score': score_row['geometry_score'],
                    'overall_score': score_row['overall_score'],
                    'match_reason': score_row['match_reason'],
                })

    return {
        'source_routes': source_rows['routes'],
        'osm_route_relations': osm_rows['osm_route_relations'],
        'line_families': line_family_rows,
        'itineraries': itinerary_rows,
        'stop_calls': stop_call_rows,
        'line_family_matches': line_family_match_rows,
        'itinerary_matches': itinerary_match_rows,
        'matched_routes': len(line_family_match_rows),
        'unknown_source_stop_references': source_rows['unknown_stop_references'],
    }
