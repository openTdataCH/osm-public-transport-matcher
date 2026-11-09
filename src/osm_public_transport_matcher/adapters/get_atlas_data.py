"""Swiss ATLAS selection and the GTFS-derived source route products of the Swiss integration."""
import hashlib
import json
import requests
import zipfile
import io
import pandas as pd
import os
import datetime
from typing import Optional


from .geo_utils import filter_points_in_switzerland
from osm_public_transport_matcher.profiles.switzerland import normalize_route_id


ATLAS_ACTUAL_DATE_RESOURCE_PERMALINK = (
    "https://data.opentransportdata.swiss/dataset/traffic-point-v2/"
    "resource_permalink/actual-date-world-traffic-point.csv"
)
ATLAS_REQUIRED_COLUMNS = ('sloid', 'uicCountryCode', 'wgs84North', 'wgs84East', 'validTo')
# Read as empty values when absent, which weakens matching without failing it.
ATLAS_EXPECTED_COLUMNS = (
    'designationOfficial', 'designation', 'number', 'servicePointBusinessOrganisation',
    'servicePointBusinessOrganisationAbbreviationEn', 'servicePointBusinessOrganisationDescriptionEn',
)


def _ensure_parent_dir(path: str) -> None:
    """Create the output parent directory if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_atlas_dataframe(response: requests.Response) -> pd.DataFrame:
    payload = io.BytesIO(response.content)
    if zipfile.is_zipfile(payload):
        payload.seek(0)
        print("ATLAS: download successful, extracting ZIP file...")
        with zipfile.ZipFile(payload) as z:
            csv_files = z.namelist()
            print("ATLAS: files in ZIP:", csv_files)

            if not csv_files:
                raise Exception("No CSV file found in the ZIP archive.")

            csv_filename = csv_files[0]
            print("ATLAS: extracting:", csv_filename)

            with z.open(csv_filename) as extracted_file:
                return pd.read_csv(extracted_file, sep=";")

    payload.seek(0)
    print("ATLAS: download successful, reading CSV file...")
    return pd.read_csv(payload, sep=";", encoding="utf-8-sig")


def _safe_direction_id(val):
    try:
        if pd.isna(val):
            return None
        return str(int(float(val)))
    except (TypeError, ValueError):
        return None


def _hash_stop_sequence(stop_keys: list[str]) -> str:
    digest = hashlib.sha1('>'.join(stop_keys).encode('utf-8')).hexdigest()
    return digest[:16]


def _first_non_empty(values) -> str | None:
    for value in values:
        if pd.notna(value):
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def _parse_uic_from_gtfs_stop_id(stop_id: object) -> str | None:
    if pd.isna(stop_id):
        return None
    prefix = str(stop_id).strip().split(':', 1)[0]
    return prefix if prefix.isdigit() else None


def _station_number(stop_id: object, original_stop_id: object, didok: object) -> str | None:
    """The station number of a GTFS stop: the numeric prefix of its ID, else the feed's ``didok``.

    Older Swiss stop IDs start with the number (``8501075:0:1``). SLOID stop IDs
    do not (``ch:1:sloid:1075``), and the feed gives the number in ``didok``.
    """
    parsed = _parse_uic_from_gtfs_stop_id(original_stop_id) or _parse_uic_from_gtfs_stop_id(stop_id)
    if parsed or didok is None or pd.isna(didok):
        return parsed
    text = str(didok).strip().removesuffix('.0')
    return text if text.isdigit() else None


def _itinerary_sequence_key(stop_id: str, sloid: object, original_stop_id: object) -> str:
    uic_number = _parse_uic_from_gtfs_stop_id(original_stop_id) or _parse_uic_from_gtfs_stop_id(stop_id)
    if uic_number:
        return f'uic:{uic_number}'
    if pd.notna(sloid):
        return str(sloid)
    return f'gtfs:{stop_id}'


def _itinerary_bucket_key(
    representative_headsign: str | None,
    trip_short_name: str | None,
    fallback_pattern_hash: str,
) -> str:
    bucket_value = _first_non_empty([representative_headsign, trip_short_name])
    if bucket_value is None:
        return fallback_pattern_hash
    return bucket_value


def _merge_stop_row(existing_row: dict[str, object], new_row: dict[str, object]) -> None:
    for field_name in (
        'stop_id',
        'sloid',
        'mapping_method',
        'stop_name',
        'platform_code',
        'original_stop_id',
        'location_type',
        'parent_station',
        'canonical_stop_key',
        'uic_number',
    ):
        if existing_row.get(field_name) is None and new_row.get(field_name) is not None:
            existing_row[field_name] = new_row[field_name]
    existing_row['sloid_variants'].update(new_row.get('sloid_variants') or set())


def _build_direction_label_from_stop_rows(stop_rows: list[dict[str, object]]) -> str | None:
    if not stop_rows:
        return None

    first_stop_name = _first_non_empty([stop_rows[0].get('stop_name')])
    last_stop_name = _first_non_empty([stop_rows[-1].get('stop_name')])
    if first_stop_name is None or last_stop_name is None:
        return None
    return f'{first_stop_name} -> {last_stop_name}'


def _build_stop_meta_lookup(gtfs_data) -> dict[str, dict[str, object]]:
    if not gtfs_data or not isinstance(gtfs_data, dict) or 'stops' not in gtfs_data:
        return {}
    columns = ['stop_id', 'stop_name', 'platform_code', 'original_stop_id', 'location_type', 'parent_station']
    columns += [column for column in ('didok',) if column in gtfs_data['stops'].columns]
    stop_meta = gtfs_data['stops'][columns].drop_duplicates(subset=['stop_id'])
    return {
        str(row['stop_id']): row
        for row in stop_meta.to_dict(orient='records')
        if pd.notna(row.get('stop_id'))
    }


def _build_match_lookup(integrated_data: pd.DataFrame) -> dict[str, dict[str, object]]:
    if integrated_data is None or integrated_data.empty:
        return {}
    match_rows = integrated_data[['stop_id', 'sloid', 'match_method']].drop_duplicates(subset=['stop_id'])
    return {
        str(row['stop_id']): row
        for row in match_rows.to_dict(orient='records')
        if pd.notna(row.get('stop_id'))
    }


def _build_trip_lookup(gtfs_data) -> dict[str, dict[str, object]]:
    if not gtfs_data or not isinstance(gtfs_data, dict) or 'trips' not in gtfs_data:
        return {}
    trip_columns = ['trip_id', 'route_id', 'direction_id', 'trip_headsign', 'trip_short_name', 'shape_id']
    trips = gtfs_data['trips'][trip_columns]
    if gtfs_data.get('trip_patterns_path'):
        from .gtfs_columnar import connect
        with connect() as con:
            representative_ids = con.execute('SELECT trip_id FROM read_parquet(?)',
                                             [gtfs_data['trip_patterns_path']]).df()['trip_id']
        trips = trips.loc[trips['trip_id'].isin(representative_ids)]
    trips = trips.drop_duplicates(subset=['trip_id'])
    return {
        str(row['trip_id']): row
        for row in trips.to_dict(orient='records')
        if pd.notna(row.get('trip_id'))
    }


def _build_itinerary_stop_info_lookup(
    stop_meta_lookup: dict[str, dict[str, object]],
    match_lookup: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    stop_ids = set(stop_meta_lookup) | set(match_lookup)
    stop_info_lookup: dict[str, dict[str, object]] = {}
    for stop_id in stop_ids:
        match_row = match_lookup.get(stop_id, {})
        stop_meta = stop_meta_lookup.get(stop_id, {})
        sloid = match_row.get('sloid')
        has_sloid = pd.notna(sloid)
        original_stop_id = stop_meta.get('original_stop_id')
        uic_number = _station_number(stop_id, original_stop_id, stop_meta.get('didok'))
        stop_info_lookup[stop_id] = {
            'stop_id': stop_id,
            'sloid': sloid,
            'sloid_variants': {str(sloid)} if has_sloid else set(),
            'mapping_method': match_row.get('match_method'),
            'stop_name': stop_meta.get('stop_name'),
            'platform_code': stop_meta.get('platform_code'),
            'original_stop_id': original_stop_id,
            'location_type': stop_meta.get('location_type'),
            'parent_station': stop_meta.get('parent_station'),
            'canonical_stop_key': sloid if has_sloid else f'gtfs:{stop_id}',
            'uic_number': uic_number,
            'sequence_key': _itinerary_sequence_key(stop_id, sloid, original_stop_id),
        }
    return stop_info_lookup


def _itinerary_stop_info_for_stop_id(
    stop_id: str,
    stop_info_lookup: dict[str, dict[str, object]],
) -> dict[str, object]:
    stop_info = stop_info_lookup.get(stop_id)
    if stop_info is not None:
        return stop_info

    stop_info = {
        'stop_id': stop_id,
        'sloid': None,
        'sloid_variants': set(),
        'mapping_method': None,
        'stop_name': None,
        'platform_code': None,
        'original_stop_id': None,
        'location_type': None,
        'parent_station': None,
        'canonical_stop_key': f'gtfs:{stop_id}',
        'uic_number': _parse_uic_from_gtfs_stop_id(stop_id),
        'sequence_key': _itinerary_sequence_key(stop_id, None, None),
    }
    stop_info_lookup[stop_id] = stop_info
    return stop_info


def _build_stop_rows_for_itinerary(
    ordered_calls: pd.DataFrame | dict,
    stop_info_lookup: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    stop_rows: list[dict[str, object]] = []
    for raw_stop_id, sequence in zip(ordered_calls['stop_id'], ordered_calls['stop_sequence']):
        stop_id = str(raw_stop_id)
        stop_info = _itinerary_stop_info_for_stop_id(stop_id, stop_info_lookup)
        stop_rows.append({
            'stop_sequence': int(sequence),
            'stop_id': stop_id,
            'sloid': stop_info['sloid'],
            'sloid_variants': set(stop_info['sloid_variants']),
            'mapping_method': stop_info['mapping_method'],
            'stop_name': stop_info['stop_name'],
            'platform_code': stop_info['platform_code'],
            'original_stop_id': stop_info['original_stop_id'],
            'location_type': stop_info['location_type'],
            'parent_station': stop_info['parent_station'],
            'canonical_stop_key': stop_info['canonical_stop_key'],
            'uic_number': stop_info['uic_number'],
        })
    return stop_rows


def _iter_trip_groups_from_staged_stop_times(trip_stop_times_path: str, chunk_size: int = 500000):
    if not trip_stop_times_path or not os.path.exists(trip_stop_times_path):
        return

    carryover = None
    chunks_seen = 0
    for chunk in pd.read_csv(
        trip_stop_times_path,
        dtype={'trip_id': str, 'stop_id': str, 'stop_sequence': int},
        chunksize=chunk_size,
        low_memory=False,
    ):
        chunks_seen += 1
        if carryover is not None:
            chunk = pd.concat([carryover, chunk], ignore_index=True)
            carryover = None
        if chunk.empty:
            if chunks_seen % 10 == 0:
                print(f"Swiss itineraries: replayed {chunks_seen} staged stop-time chunks…")
            continue

        last_trip_id = chunk.iloc[-1]['trip_id']
        last_trip_mask = chunk['trip_id'] == last_trip_id
        if last_trip_mask.all():
            carryover = chunk
            if chunks_seen % 10 == 0:
                print(f"Swiss itineraries: replayed {chunks_seen} staged stop-time chunks…")
            continue

        current_chunk = chunk.loc[~last_trip_mask].copy()
        carryover = chunk.loc[last_trip_mask].copy()

        for trip_id, trip_group in current_chunk.groupby('trip_id', sort=False):
            yield str(trip_id), trip_group.sort_values(by=['stop_sequence', 'stop_id']).reset_index(drop=True)

        if chunks_seen % 10 == 0:
            print(f"Swiss itineraries: replayed {chunks_seen} staged stop-time chunks…")

    if carryover is not None and not carryover.empty:
        for trip_id, trip_group in carryover.groupby('trip_id', sort=False):
            yield str(trip_id), trip_group.sort_values(by=['stop_sequence', 'stop_id']).reset_index(drop=True)


def _build_itinerary_frames(
    gtfs_data,
    integrated_data: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if integrated_data is None or integrated_data.empty:
        return pd.DataFrame(), pd.DataFrame()

    match_lookup = _build_match_lookup(integrated_data)
    stop_meta_lookup = _build_stop_meta_lookup(gtfs_data)
    trip_lookup = _build_trip_lookup(gtfs_data)
    stop_info_lookup = _build_itinerary_stop_info_lookup(stop_meta_lookup, match_lookup)

    trip_stop_times_path = None
    if gtfs_data and isinstance(gtfs_data, dict):
        trip_stop_times_path = gtfs_data.get('trip_stop_times_path')

    trip_patterns_path = gtfs_data.get('trip_patterns_path') if gtfs_data else None
    if not trip_stop_times_path and not trip_patterns_path:
        fallback_columns = ['route_id', 'direction_id', 'stop_id', 'sloid', 'match_method']
        if 'direction' in integrated_data.columns:
            fallback_columns.append('direction')
        trip_calls = integrated_data[fallback_columns].copy()
        trip_calls['trip_id'] = (
            trip_calls['route_id'].astype(str)
            + ':'
            + trip_calls['direction_id'].apply(_safe_direction_id).fillna('na')
            + ':synthetic'
        )
        trip_calls['stop_sequence'] = trip_calls.groupby(['route_id', 'direction_id']).cumcount()
        if stop_meta_lookup:
            stop_meta = pd.DataFrame(stop_meta_lookup.values())
            trip_calls = trip_calls.merge(stop_meta, on='stop_id', how='left')
        else:
            for column_name in ['stop_name', 'platform_code', 'original_stop_id', 'location_type', 'parent_station']:
                trip_calls[column_name] = None
        trip_calls['trip_headsign'] = None
        trip_calls['trip_short_name'] = None
        trip_calls['shape_id'] = None
        trip_calls['direction_id'] = trip_calls['direction_id'].apply(_safe_direction_id)
        trip_calls['canonical_stop_key'] = trip_calls['sloid'].where(
            trip_calls['sloid'].notna(),
            'gtfs:' + trip_calls['stop_id'].astype(str),
        )

    itinerary_rows = {}
    itinerary_pattern_rows_by_key: dict[
        tuple[str, str | None, str],
        dict[str, dict[str, object]],
    ] = {}

    if trip_patterns_path:
        from .gtfs_columnar import iter_trip_patterns
        trip_groups = iter_trip_patterns(trip_patterns_path)
    elif trip_stop_times_path:
        trip_groups = ((trip_id, calls, 1) for trip_id, calls in _iter_trip_groups_from_staged_stop_times(trip_stop_times_path))
    else:
        if trip_calls.empty:
            return pd.DataFrame(), pd.DataFrame()
        trip_groups = (
            (str(trip_id), trip_group.sort_values(by=['stop_sequence', 'stop_id']).reset_index(drop=True), 1)
            for trip_id, trip_group in trip_calls.groupby('trip_id', sort=False)
        )

    processed_trip_count = 0
    processed_stop_call_count = 0
    materialized_trip_pattern_count = 0
    skipped_duplicate_trip_pattern_count = 0
    progress_trip_interval = 10000

    for trip_id, ordered_calls, trip_weight in trip_groups:
        processed_trip_count += trip_weight
        trip_meta = trip_lookup.get(trip_id)
        if trip_meta is None and (trip_stop_times_path or trip_patterns_path):
            continue

        route_id = str(trip_meta['route_id']) if trip_meta is not None else str(ordered_calls.iloc[0]['route_id'])
        direction_id = _safe_direction_id(trip_meta.get('direction_id') if trip_meta is not None else ordered_calls.iloc[0].get('direction_id'))
        representative_headsign = None
        trip_short_name = None
        if trip_meta is not None:
            representative_headsign = _first_non_empty([trip_meta.get('trip_headsign')])
            trip_short_name = _first_non_empty([trip_meta.get('trip_short_name')])

        if len(ordered_calls['stop_id']) == 0:
            continue

        ordered_stop_ids = tuple(str(stop_id) for stop_id in ordered_calls['stop_id'])
        sequence_stop_keys = [
            _itinerary_stop_info_for_stop_id(stop_id, stop_info_lookup)['sequence_key']
            for stop_id in ordered_stop_ids
        ]
        fallback_pattern_hash = _hash_stop_sequence(sequence_stop_keys)
        itinerary_bucket_key = _itinerary_bucket_key(
            representative_headsign,
            trip_short_name,
            fallback_pattern_hash,
        )
        itinerary_key = (route_id, direction_id, itinerary_bucket_key)
        itinerary_id = f"{route_id}:{direction_id or 'na'}:{itinerary_bucket_key}"

        existing_row = itinerary_rows.get(itinerary_key)
        if existing_row is None:
            itinerary_rows[itinerary_key] = {
                'itinerary_id': itinerary_id,
                'route_id': route_id,
                'direction_id': direction_id,
                'direction_label': None,
                'representative_headsign': representative_headsign,
                'trip_count': trip_weight,
                'shape_id': None,
                'headsign_or_pattern_hash': itinerary_bucket_key,
            }
        else:
            existing_row['trip_count'] += trip_weight
            if existing_row['representative_headsign'] is None and representative_headsign is not None:
                existing_row['representative_headsign'] = representative_headsign

        pattern_candidates = itinerary_pattern_rows_by_key.setdefault(itinerary_key, {})
        pattern_candidate = pattern_candidates.get(fallback_pattern_hash)
        if pattern_candidate is None:
            stop_rows_for_itinerary = _build_stop_rows_for_itinerary(ordered_calls, stop_info_lookup)
            pattern_candidates[fallback_pattern_hash] = {
                'trip_count': trip_weight,
                'stop_rows': stop_rows_for_itinerary,
                'uic_sequence': [k.replace('uic:', '') for k in sequence_stop_keys],
                'seen_stop_id_sequences': {ordered_stop_ids},
            }
            materialized_trip_pattern_count += 1
            processed_stop_call_count += len(stop_rows_for_itinerary)
        else:
            pattern_candidate['trip_count'] += trip_weight
            seen_stop_id_sequences = pattern_candidate.setdefault('seen_stop_id_sequences', set())
            if ordered_stop_ids in seen_stop_id_sequences:
                skipped_duplicate_trip_pattern_count += trip_weight
            else:
                stop_rows_for_itinerary = _build_stop_rows_for_itinerary(ordered_calls, stop_info_lookup)
                for existing_stop_row, stop_row in zip(pattern_candidate['stop_rows'], stop_rows_for_itinerary):
                    _merge_stop_row(existing_stop_row, stop_row)
                seen_stop_id_sequences.add(ordered_stop_ids)
                materialized_trip_pattern_count += 1

        if processed_trip_count % progress_trip_interval == 0:
            print(
                "Swiss itineraries: processed "
                f"{processed_trip_count:,} trips, "
                f"{len(itinerary_rows):,} unique itineraries, "
                f"{processed_stop_call_count:,} emitted stop calls, "
                f"{skipped_duplicate_trip_pattern_count:,} duplicate trip patterns skipped…"
            )

    if not itinerary_rows:
        return pd.DataFrame(), pd.DataFrame()

    print(
        "Swiss itineraries: completed reduction with "
        f"{processed_trip_count:,} trips, "
        f"{len(itinerary_rows):,} unique itineraries, "
        f"{processed_stop_call_count:,} emitted stop calls, "
        f"{materialized_trip_pattern_count:,} materialized trip patterns, "
        f"{skipped_duplicate_trip_pattern_count:,} duplicate trip patterns skipped"
    )

    itinerary_stop_rows: list[dict[str, object]] = []
    for itinerary_key, pattern_candidates in itinerary_pattern_rows_by_key.items():
        itinerary_id = itinerary_rows[itinerary_key]['itinerary_id']
        _, selected_pattern = max(
            pattern_candidates.items(),
            key=lambda item: (
                int(item[1]['trip_count']),
                len(item[1]['stop_rows']),
                item[0],
            ),
        )
        itinerary_rows[itinerary_key]['shape_id'] = ">".join(selected_pattern['uic_sequence'])
        itinerary_rows[itinerary_key]['direction_label'] = _build_direction_label_from_stop_rows(selected_pattern['stop_rows'])
        stop_rows = selected_pattern['stop_rows']
        for stop_row in stop_rows:
            itinerary_stop_rows.append({
                'itinerary_id': itinerary_id,
                'stop_sequence': stop_row['stop_sequence'],
                'source_stop_id': stop_row['stop_id'],
                'source_stop_key': stop_row['sloid'],
                'source_stop_key_variants': json.dumps(sorted(stop_row['sloid_variants'])) if stop_row['sloid_variants'] else None,
                'mapping_method': stop_row['mapping_method'],
                'stop_label': stop_row['stop_name'],
                'platform_code': stop_row['platform_code'],
                'original_stop_id': stop_row['original_stop_id'],
                'location_type': stop_row['location_type'],
                'parent_station': stop_row['parent_station'],
                'canonical_stop_key': stop_row['canonical_stop_key'],
                'station_ref': stop_row['uic_number'],
            })

    itineraries_df = pd.DataFrame(itinerary_rows.values())
    itinerary_stop_calls_df = pd.DataFrame(itinerary_stop_rows)
    itineraries_df = itineraries_df.sort_values(by=['route_id', 'direction_id', 'itinerary_id']).reset_index(drop=True)
    itinerary_stop_calls_df = itinerary_stop_calls_df.sort_values(by=['itinerary_id', 'stop_sequence']).reset_index(drop=True)
    return itineraries_df, itinerary_stop_calls_df


def get_current_gtfs_permalink(year: Optional[int] = None, locale: str = "en") -> str:
    """Return the OpenTransportData GTFS permalink for the active timetable year."""
    target_year = int(year) if year is not None else datetime.date.today().year
    return f"https://data.opentransportdata.swiss/{locale}/dataset/timetable-{target_year}-gtfs2020/permalink"


def get_atlas_stops(output_path, download_url, *, boundary_geojson=None):
    """Download and process ATLAS stops data.

    Returns:
        dict: Filter statistics collected during processing, suitable for
              merging into stats.json under the ``atlas_filtering`` key.
    """
    response = requests.get(download_url, timeout=120)
    response.raise_for_status()

    df = _load_atlas_dataframe(response)
    missing = [column for column in ATLAS_REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"ATLAS CSV lacks required columns: {missing}")
    warnings = []
    absent = [column for column in ATLAS_EXPECTED_COLUMNS if column not in df.columns]
    if absent:
        warnings.append(f"ATLAS CSV lacks columns {absent}; their values are empty")

    # ── Step 1: Country filter (uicCountryCode == 85) ──────────────
    raw_total = len(df)
    df = df[df['uicCountryCode'] == 85]
    after_country = len(df)

    # ── Step 2: Geography filter (inside Swiss border polygon) ──────
    geography_options = {'boundary_geojson': str(boundary_geojson)} if boundary_geojson is not None else {}
    df = filter_points_in_switzerland(df, lat_col='wgs84North', lon_col='wgs84East', **geography_options)
    after_geo = len(df)

    # ── Step 3: Validity filter (validTo >= today) ──────────────────
    # Note: pandas datetime64[ns] cannot represent years > 2262,
    # turning '9999-12-31' into NaT.  Compare ISO strings directly.
    today_iso = datetime.date.today().isoformat()
    valid_to_iso = df['validTo'].astype(str).str.slice(0, 10)
    df = df[valid_to_iso >= today_iso].copy()
    after_validity = len(df)
    print(
        f"ATLAS: filtered {after_geo - after_validity:,} rows with past "
        f"validTo dates, kept {after_validity:,} rows"
    )

    # ── Step 4: Type filter (BOARDING_PLATFORM) ─────────────────────
    # Collect type counts from the full (pre-type-filter) dataset so we
    # can report every type present in the raw Swiss data.
    type_counts: dict = {}
    if 'trafficPointElementType' in df.columns:
        type_counts = df['trafficPointElementType'].value_counts(dropna=False).to_dict()
        # Convert numpy int64 keys/values to plain Python types
        type_counts = {str(k): int(v) for k, v in type_counts.items()}

        df = df[df['trafficPointElementType'] == 'BOARDING_PLATFORM'].copy()
        print(
            f"ATLAS: filtered to BOARDING_PLATFORM, kept {len(df):,} "
            f"(from {after_validity:,})"
        )
    else:
        warnings.append("ATLAS CSV lacks trafficPointElementType; all traffic point types were kept, "
                        "not only boarding platforms")
    for warning in warnings:
        print(f"ATLAS warning: {warning}")

    after_type = len(df)

    # ── Save processed data ─────────────────────────────────────────
    _ensure_parent_dir(output_path)
    df.to_csv(output_path, sep=";", index=False)
    print(f"ATLAS: total BOARDING_PLATFORM rows kept = {after_type:,}")
    print(f"ATLAS: processed CSV saved to: {output_path}")

    # ── Build and return filter statistics ──────────────────────────
    def _pct(part: int, total: int) -> float:
        return round(part / total * 100, 1) if total else 0.0

    boarding_platform_count = type_counts.get('BOARDING_PLATFORM', 0)
    boarding_area_count = type_counts.get('BOARDING_AREA', 0)

    return {
        "downloaded_at": today_iso,
        "raw_total": raw_total,
        "after_uic_number_filter": after_country,
        "after_geo_filter": after_geo,
        "after_validity_filter": after_validity,
        "after_type_filter": after_type,
        "eliminated_by_uic_number": raw_total - after_country,
        "eliminated_by_geo": after_country - after_geo,
        "eliminated_by_validity": after_geo - after_validity,
        "eliminated_by_type": after_validity - after_type,
        "type_counts": type_counts,
        "boarding_platform_pct": _pct(boarding_platform_count, raw_total),
        "boarding_area_pct": _pct(boarding_area_count, raw_total),
        "warnings": warnings,
    }


def swiss_route_products(gtfs_data, integrated_data: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the source route products from GTFS routes whose stops are resolved to SLOIDs."""
    routes = integrated_data[
        ['route_id', 'agency_id', 'route_short_name', 'route_long_name', 'route_desc', 'route_type']
    ].drop_duplicates('route_id').copy()
    routes['route_id_normalized'] = routes['route_id'].map(normalize_route_id)
    itineraries, stop_calls = _build_itinerary_frames(gtfs_data, integrated_data)
    return {'source_routes': routes, 'source_itineraries': itineraries, 'source_stop_calls': stop_calls}
