"""Read optional route products at an explicit adapter boundary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

SOURCE_ROUTE_PRODUCTS = ('source_routes', 'source_itineraries', 'source_stop_calls')


def load_source_route_products(processed_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Load present source route products; malformed existing products fail the run clearly."""
    result = {}
    for key in SOURCE_ROUTE_PRODUCTS:
        path = Path(processed_dir) / f'{key}.csv'
        if path.exists():
            try:
                result[key] = pd.read_csv(path, dtype=str, keep_default_na=False)
            except pd.errors.EmptyDataError:
                result[key] = pd.DataFrame()
    return result


def read_gtfs_identity_cache(processed_dir: str | Path) -> tuple[list[dict], list[dict]]:
    paths = [Path(processed_dir) / filename for filename in (
        'gtfs_stops_raw.csv', 'gtfs_stop_identity_resolution.csv',
    )]
    if not any(path.exists() for path in paths):
        return [], []
    if not all(path.exists() for path in paths):
        raise ValueError('Both GTFS stop and identity cache products are required together')
    rows = []
    numeric_columns = {
        'stop_lat', 'stop_lon', 'gtfs_stop_lat', 'gtfs_stop_lon',
        'atlas_lat', 'atlas_lon', 'confidence', 'distance_m',
    }
    for path in paths:
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            rows.append([])
            continue
        product = []
        for raw in frame.to_dict(orient='records'):
            row = {key: (value if value != '' else None) for key, value in raw.items()}
            for key in numeric_columns & row.keys():
                row[key] = float(row[key]) if row[key] is not None else None
            if row.get('details_json'):
                row['details_json'] = json.loads(row['details_json'])
                if not isinstance(row['details_json'], dict):
                    raise ValueError(f'{path}: details_json must contain an object')
            product.append(row)
        rows.append(product)
    return rows[0], rows[1]


def source_route_evidence(route_data: dict[str, pd.DataFrame]) -> dict[str, dict[str, list]]:
    """Build optional stop evidence without flattening ordered stop-call products."""
    if not all(key in route_data and not route_data[key].empty for key in SOURCE_ROUTE_PRODUCTS):
        return {}
    routes = {str(row['route_id']): row for row in route_data['source_routes'].to_dict('records')}
    itineraries = {str(row['itinerary_id']): row for row in route_data['source_itineraries'].to_dict('records')}
    evidence = {}
    seen = set()
    for row in route_data['source_stop_calls'].to_dict('records'):
        source_key = row.get('source_stop_key')
        itinerary = itineraries.get(str(row.get('itinerary_id')), {})
        route_id = itinerary.get('route_id')
        if not source_key or not route_id:
            continue
        direction = itinerary.get('direction_id')
        if direction not in (None, ''):
            try:
                direction = str(int(float(direction)))
            except (ValueError, TypeError):
                direction = str(direction)
        else:
            direction = None
        identity = (str(source_key), str(route_id), direction)
        if identity in seen:
            continue
        seen.add(identity)
        route = routes.get(str(route_id), {})
        evidence.setdefault(str(source_key), {'gtfs': []})['gtfs'].append({
            'route_id': str(route_id),
            'route_id_normalized': route.get('route_id_normalized'),
            'route_name_short': route.get('route_short_name'),
            'route_name_long': route.get('route_long_name'),
            'direction_id': direction,
            'direction_name': itinerary.get('direction_label'),
        })
    return evidence


def scope_source_stop_keys(route_data: dict[str, pd.DataFrame], keys_by_id: dict[str, str]) -> dict[str, pd.DataFrame]:
    """Translate native source references to stable engine keys, keeping foreign keys."""
    result = dict(route_data)
    calls = result.get('source_stop_calls')
    if calls is None:
        return result
    calls = calls.copy()
    result['source_stop_calls'] = calls
    for column in ('source_stop_key', 'canonical_stop_key'):
        if column in calls:
            calls[column] = calls[column].map(lambda value: keys_by_id.get(str(value), value))
    if 'source_stop_key_variants' in calls:
        def scope_variants(value: Any) -> Any:
            if not isinstance(value, str) or not value:
                return value
            return json.dumps([keys_by_id.get(str(item), item) for item in json.loads(value)])
        calls['source_stop_key_variants'] = calls['source_stop_key_variants'].map(scope_variants)
    return result


def unresolved_osm_member_diagnostics(route_data: dict[str, pd.DataFrame]) -> list[dict]:
    """Describe stop members outside the currently supported geometry extract."""
    members = route_data.get('osm_route_relation_members')
    if members is None or members.empty:
        return []
    # Route extracts can contain millions of way members. Filter the two
    # relevant columns before materializing at most ten example dictionaries.
    roles = members.get('member_role', pd.Series('', index=members.index)).fillna('').astype(str)
    resolved = members.get('resolved_node_id', pd.Series(None, index=members.index, dtype=object))
    missing = roles.str.startswith(('stop', 'platform')) & (resolved.isna() | resolved.eq(''))
    count = int(missing.sum())
    if not count:
        return []
    examples = members.loc[missing, ['relation_id', 'member_type', 'member_ref']].head(10).to_dict('records')
    return [{
        'code': 'unsupported_or_external_osm_route_members',
        'stage': 'osm_adapter',
        'count': count,
        'reason': 'Stop members without supported geometry are retained in raw inputs but excluded from normalized itinerary calls.',
        'examples': [
            {'relation_id': row.get('relation_id'), 'element_type': row.get('member_type'), 'element_id': row.get('member_ref')}
            for row in examples
        ],
    }]
