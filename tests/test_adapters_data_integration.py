"""
Tests for data integration and processing pipelines.
Verifies the correct flow of data through the various processing stages.
"""
import json
import re
import pandas as pd
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from public_transport_matcher.adapters.get_atlas_data import (
    ATLAS_ACTUAL_DATE_RESOURCE_PERMALINK,
    _build_itinerary_frames,
    get_atlas_stops,
    get_current_gtfs_permalink,
    swiss_route_products,
)

class TestGtfsRoutesIntegration:
    """Tests for the Swiss source route products built from integrated GTFS data."""

    def test_gtfs_precomputed_integration_is_used(self, tmp_path):
        """
        Verify that precomputed integrated GTFS data is correctly used.
        
        Integrated GTFS rows become routes, itineraries and stop calls
        without being re-processed or ignored.
        """
        # 1. Setup Mock Data
        
        stop_times_path = tmp_path / "trip_stop_times.csv"
        pd.DataFrame([
            {'trip_id': 'trip-1', 'stop_id': 'stop-1a', 'stop_sequence': 1},
            {'trip_id': 'trip-1', 'stop_id': 'stop-1b', 'stop_sequence': 2},
            {'trip_id': 'trip-2', 'stop_id': 'stop-2a', 'stop_sequence': 1},
            {'trip_id': 'trip-2', 'stop_id': 'stop-2b', 'stop_sequence': 2},
        ]).to_csv(stop_times_path, index=False)

        gtfs_stream = {
            'trip_stop_times_path': str(stop_times_path),
            'trips': pd.DataFrame([
                {'trip_id': 'trip-1', 'route_id': 'gtfs-route-1', 'direction_id': '0', 'trip_headsign': 'End 1', 'trip_short_name': None, 'shape_id': None},
                {'trip_id': 'trip-2', 'route_id': 'gtfs-route-2', 'direction_id': '1', 'trip_headsign': 'End 2', 'trip_short_name': None, 'shape_id': None},
            ]),
            'stops': pd.DataFrame([
                {'stop_id': 'stop-1a', 'stop_name': 'Alpha', 'platform_code': None, 'original_stop_id': '8501000:0:1', 'location_type': None, 'parent_station': None},
                {'stop_id': 'stop-1b', 'stop_name': 'Beta', 'platform_code': None, 'original_stop_id': '8502000:0:1', 'location_type': None, 'parent_station': None},
                {'stop_id': 'stop-2a', 'stop_name': 'Gamma', 'platform_code': None, 'original_stop_id': '8503000:0:1', 'location_type': None, 'parent_station': None},
                {'stop_id': 'stop-2b', 'stop_name': 'Delta', 'platform_code': None, 'original_stop_id': '8504000:0:1', 'location_type': None, 'parent_station': None},
            ]),
        }
        
        # Precomputed Integrated GTFS Data
        # Create a DataFrame that mimics build_integrated_gtfs_data_streaming output
        integrated_gtfs_data = pd.DataFrame([
            {
                'sloid': 'ch:1:sloid:1a',
                'stop_id': 'stop-1a',
                'match_method': 'strict',
                'route_id': 'gtfs-route-1',
                'agency_id': 'agency-1',
                'route_short_name': 'R1',
                'route_long_name': 'Route 1',
                'route_desc': 'Desc 1',
                'route_type': '3',
                'direction_id': 0,
                'direction': 'Ignored -> Label'
            },
            {
                'sloid': 'ch:1:sloid:1b',
                'stop_id': 'stop-1b',
                'match_method': 'strict',
                'route_id': 'gtfs-route-1',
                'agency_id': 'agency-1',
                'route_short_name': 'R1',
                'route_long_name': 'Route 1',
                'route_desc': 'Desc 1',
                'route_type': '3',
                'direction_id': 0,
                'direction': 'Ignored -> Label'
            },
            {
                'sloid': 'ch:1:sloid:2a',
                'stop_id': 'stop-2a',
                'match_method': 'coordinate_proximity',
                'route_id': 'gtfs-route-2',
                'agency_id': 'agency-2',
                'route_short_name': 'R2',
                'route_long_name': 'Route 2',
                'route_desc': 'Desc 2',
                'route_type': '3',
                'direction_id': 1,
                'direction': 'Ignored -> Label'
            },
            {
                'sloid': 'ch:1:sloid:2b',
                'stop_id': 'stop-2b',
                'match_method': 'coordinate_proximity',
                'route_id': 'gtfs-route-2',
                'agency_id': 'agency-2',
                'route_short_name': 'R2',
                'route_long_name': 'Route 2',
                'route_desc': 'Desc 2',
                'route_type': '3',
                'direction_id': 1,
                'direction': 'Ignored -> Label'
            }
        ])
        
        # 2. Execute Function
        products = swiss_route_products(gtfs_stream, integrated_gtfs_data)

        # 3. Verify Output
        routes_df = products['source_routes']
        itineraries_df = products['source_itineraries']
        stop_calls_df = products['source_stop_calls']

        assert len(routes_df) == 2, f"Expected 2 route rows, got {len(routes_df)}"
        expected_route_cols = {
            'route_id',
            'agency_id',
            'route_id_normalized',
            'route_short_name',
            'route_long_name',
            'route_desc',
            'route_type',
        }
        assert expected_route_cols.issubset(set(routes_df.columns)), "Route columns are missing"
        assert set(routes_df['route_id']) == {'gtfs-route-1', 'gtfs-route-2'}

        assert len(itineraries_df) == 2, f"Expected 2 itinerary rows, got {len(itineraries_df)}"
        expected_direction_cols = {
            'itinerary_id',
            'route_id',
            'direction_id',
            'representative_headsign',
            'direction_label',
        }
        assert expected_direction_cols.issubset(set(itineraries_df.columns)), "Itinerary columns are missing"
        assert set(itineraries_df['direction_label']) == {'Alpha -> Beta', 'Gamma -> Delta'}

        assert len(stop_calls_df) == 4, f"Expected 4 stop-call rows, got {len(stop_calls_df)}"
        expected_stop_cols = {
            'itinerary_id',
            'source_stop_key',
            'source_stop_id',
            'stop_sequence',
            'mapping_method',
        }
        assert expected_stop_cols.issubset(set(stop_calls_df.columns)), "Stop-call columns are missing"
        assert set(stop_calls_df['source_stop_key']) == {'ch:1:sloid:1a', 'ch:1:sloid:1b', 'ch:1:sloid:2a', 'ch:1:sloid:2b'}
        assert set(stop_calls_df['mapping_method']) == {'strict', 'coordinate_proximity'}

    def test_gtfs_only_integration(self, tmp_path):
        """Integrated rows without staged trips still produce route products."""
        gtfs_stream = {}
        
        # GTFS Input
        integrated_gtfs = pd.DataFrame([{
            'sloid': 'ch:1:sloid:1',
            'stop_id': 'stop-1',
            'match_method': 'unique_number',
            'route_id': 'r1',
            'agency_id': 'agency-1',
            'route_short_name': 'R1',
            'route_long_name': 'Route 1',
            'route_desc': 'Desc 1',
            'route_type': '3',
            'direction_id': 0,
            'direction': 'dir1'
        }])
        
        # Execute
        products = swiss_route_products(gtfs_stream, integrated_gtfs)

        # Verify
        stop_calls = products['source_stop_calls']
        assert len(products['source_routes']) == 1
        assert len(products['source_itineraries']) == 1
        assert len(stop_calls) == 1
        assert stop_calls.iloc[0]['source_stop_key'] == 'ch:1:sloid:1'
        assert stop_calls.iloc[0]['mapping_method'] == 'unique_number'


def test_gtfs_permalink_uses_current_year_and_en_locale():
    current_year = date.today().year
    permalink = get_current_gtfs_permalink()

    assert f"timetable-{current_year}-gtfs2020" in permalink
    assert "/en/dataset/" in permalink


def test_gtfs_permalink_links_do_not_contain_stale_years():
    current_year = str(date.today().year)
    link_pattern = re.compile(r"timetable-(\d{4})-gtfs2020")

    import public_transport_matcher.adapters.get_atlas_data as atlas_adapter
    checked_files = [Path(atlas_adapter.__file__)]

    stale_hits = []
    for file_path in checked_files:
        content = file_path.read_text(encoding='utf-8')
        for match in link_pattern.finditer(content):
            found_year = match.group(1)
            if found_year != current_year:
                stale_hits.append((str(file_path), found_year, match.group(0)))

    assert not stale_hits, f"Found stale GTFS timetable years: {stale_hits}"


def test_atlas_actual_date_permalink_targets_v2_csv_resource():
    assert ATLAS_ACTUAL_DATE_RESOURCE_PERMALINK == (
        "https://data.opentransportdata.swiss/dataset/traffic-point-v2/"
        "resource_permalink/actual-date-world-traffic-point.csv"
    )


def test_get_atlas_stops_accepts_plain_csv_payload_with_bom(tmp_path):
    csv_payload = (
        "\ufeffsloid;uicCountryCode;validTo;trafficPointElementType;wgs84North;wgs84East\n"
        "ch:1:sloid:1;85;9999-12-31;BOARDING_PLATFORM;47.0;8.0\n"
    ).encode("utf-8")
    mocked_response = MagicMock()
    mocked_response.content = csv_payload
    mocked_response.raise_for_status.return_value = None

    output_path = tmp_path / "stops_ATLAS.csv"

    with patch("public_transport_matcher.adapters.get_atlas_data.requests.get", return_value=mocked_response), patch(
        "public_transport_matcher.adapters.get_atlas_data.filter_points_in_switzerland",
        side_effect=lambda df, lat_col, lon_col: df,
    ):
        stats = get_atlas_stops(str(output_path), ATLAS_ACTUAL_DATE_RESOURCE_PERMALINK)

    written = pd.read_csv(output_path, sep=";")

    assert list(written.columns)[0] == "sloid"
    assert written.iloc[0]["sloid"] == "ch:1:sloid:1"
    assert stats["raw_total"] == 1
    assert stats["after_type_filter"] == 1


def test_swiss_itineraries_collapse_same_uic_sequence_and_keep_sloid_variants(tmp_path):
    stop_times_path = tmp_path / "trip_stop_times.csv"
    pd.DataFrame([
        {'trip_id': 'trip-1', 'stop_id': '8501000:0:A', 'stop_sequence': 1},
        {'trip_id': 'trip-1', 'stop_id': '8502000:0:B', 'stop_sequence': 2},
        {'trip_id': 'trip-2', 'stop_id': '8501000:0:C', 'stop_sequence': 1},
        {'trip_id': 'trip-2', 'stop_id': '8502000:0:B', 'stop_sequence': 2},
    ]).to_csv(stop_times_path, index=False)

    gtfs_data = {
        'trip_stop_times_path': str(stop_times_path),
        'trips': pd.DataFrame([
            {'trip_id': 'trip-1', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': 'End', 'trip_short_name': None, 'shape_id': None},
            {'trip_id': 'trip-2', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': 'End', 'trip_short_name': None, 'shape_id': None},
        ]),
        'stops': pd.DataFrame([
            {'stop_id': '8501000:0:A', 'stop_name': 'Alpha A', 'platform_code': 'A', 'original_stop_id': '8501000:0:A', 'location_type': None, 'parent_station': None},
            {'stop_id': '8501000:0:C', 'stop_name': 'Alpha C', 'platform_code': 'C', 'original_stop_id': '8501000:0:C', 'location_type': None, 'parent_station': None},
            {'stop_id': '8502000:0:B', 'stop_name': 'Beta B', 'platform_code': 'B', 'original_stop_id': '8502000:0:B', 'location_type': None, 'parent_station': None},
        ]),
    }
    integrated = pd.DataFrame([
        {'stop_id': '8501000:0:A', 'sloid': 'ch:1:sloid:A', 'match_method': 'uic_platform'},
        {'stop_id': '8501000:0:C', 'sloid': 'ch:1:sloid:C', 'match_method': 'uic_platform'},
        {'stop_id': '8502000:0:B', 'sloid': 'ch:1:sloid:B', 'match_method': 'uic_platform'},
    ])

    itineraries_df, stop_calls_df = _build_itinerary_frames(gtfs_data, integrated)

    assert len(itineraries_df) == 1
    assert int(itineraries_df.iloc[0]['trip_count']) == 2
    assert itineraries_df.iloc[0]['direction_label'] == 'Alpha A -> Beta B'
    assert len(stop_calls_df) == 2

    first_stop = stop_calls_df.sort_values(by='stop_sequence').iloc[0]
    assert json.loads(first_stop['source_stop_key_variants']) == ['ch:1:sloid:A', 'ch:1:sloid:C']


def test_swiss_itineraries_keep_distinct_patterns_when_headsign_missing(tmp_path):
    stop_times_path = tmp_path / "trip_stop_times.csv"
    pd.DataFrame([
        {'trip_id': 'trip-1', 'stop_id': '8501000:0:A', 'stop_sequence': 1},
        {'trip_id': 'trip-1', 'stop_id': '8502000:0:B', 'stop_sequence': 2},
        {'trip_id': 'trip-2', 'stop_id': '8501000:0:A', 'stop_sequence': 1},
        {'trip_id': 'trip-2', 'stop_id': '8503000:0:C', 'stop_sequence': 2},
    ]).to_csv(stop_times_path, index=False)

    gtfs_data = {
        'trip_stop_times_path': str(stop_times_path),
        'trips': pd.DataFrame([
            {'trip_id': 'trip-1', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': None, 'trip_short_name': None, 'shape_id': None},
            {'trip_id': 'trip-2', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': None, 'trip_short_name': None, 'shape_id': None},
        ]),
        'stops': pd.DataFrame([
            {'stop_id': '8501000:0:A', 'stop_name': 'Alpha', 'platform_code': 'A', 'original_stop_id': '8501000:0:A', 'location_type': None, 'parent_station': None},
            {'stop_id': '8502000:0:B', 'stop_name': 'Beta', 'platform_code': 'B', 'original_stop_id': '8502000:0:B', 'location_type': None, 'parent_station': None},
            {'stop_id': '8503000:0:C', 'stop_name': 'Gamma', 'platform_code': 'C', 'original_stop_id': '8503000:0:C', 'location_type': None, 'parent_station': None},
        ]),
    }
    integrated = pd.DataFrame([
        {'stop_id': '8501000:0:A', 'sloid': 'ch:1:sloid:A', 'match_method': 'uic_platform'},
        {'stop_id': '8502000:0:B', 'sloid': 'ch:1:sloid:B', 'match_method': 'uic_platform'},
        {'stop_id': '8503000:0:C', 'sloid': 'ch:1:sloid:C', 'match_method': 'uic_platform'},
    ])

    itineraries_df, stop_calls_df = _build_itinerary_frames(gtfs_data, integrated)

    assert len(itineraries_df) == 2
    assert len(stop_calls_df) == 4
    assert set(itineraries_df['direction_label']) == {'Alpha -> Beta', 'Alpha -> Gamma'}


def test_swiss_itinerary_direction_label_uses_dominant_uic_subsequence(tmp_path):
    stop_times_path = tmp_path / "trip_stop_times.csv"
    pd.DataFrame([
        {'trip_id': 'trip-1', 'stop_id': '8501000:0:minor', 'stop_sequence': 1},
        {'trip_id': 'trip-1', 'stop_id': '8509000:0:minor', 'stop_sequence': 2},
        {'trip_id': 'trip-2', 'stop_id': '8501000:0:main-a', 'stop_sequence': 1},
        {'trip_id': 'trip-2', 'stop_id': '8502000:0:main', 'stop_sequence': 2},
        {'trip_id': 'trip-3', 'stop_id': '8501000:0:main-b', 'stop_sequence': 1},
        {'trip_id': 'trip-3', 'stop_id': '8502000:0:main', 'stop_sequence': 2},
    ]).to_csv(stop_times_path, index=False)

    gtfs_data = {
        'trip_stop_times_path': str(stop_times_path),
        'route_directions': pd.DataFrame([
            {'route_id': 'R1', 'direction_id': '0', 'direction': 'Wrong -> Label'},
        ]),
        'trips': pd.DataFrame([
            {'trip_id': 'trip-1', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': 'End', 'trip_short_name': None, 'shape_id': None},
            {'trip_id': 'trip-2', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': 'End', 'trip_short_name': None, 'shape_id': None},
            {'trip_id': 'trip-3', 'route_id': 'R1', 'direction_id': '0', 'trip_headsign': 'End', 'trip_short_name': None, 'shape_id': None},
        ]),
        'stops': pd.DataFrame([
            {'stop_id': '8501000:0:minor', 'stop_name': 'Minor Start', 'platform_code': None, 'original_stop_id': '8501000:0:minor', 'location_type': None, 'parent_station': None},
            {'stop_id': '8509000:0:minor', 'stop_name': 'Minor End', 'platform_code': None, 'original_stop_id': '8509000:0:minor', 'location_type': None, 'parent_station': None},
            {'stop_id': '8501000:0:main-a', 'stop_name': 'Alpha', 'platform_code': None, 'original_stop_id': '8501000:0:main-a', 'location_type': None, 'parent_station': None},
            {'stop_id': '8501000:0:main-b', 'stop_name': 'Alpha Alt', 'platform_code': None, 'original_stop_id': '8501000:0:main-b', 'location_type': None, 'parent_station': None},
            {'stop_id': '8502000:0:main', 'stop_name': 'Beta', 'platform_code': None, 'original_stop_id': '8502000:0:main', 'location_type': None, 'parent_station': None},
        ]),
    }
    integrated = pd.DataFrame([
        {'stop_id': '8501000:0:minor', 'sloid': 'ch:1:sloid:minor-start', 'match_method': 'uic_platform'},
        {'stop_id': '8509000:0:minor', 'sloid': 'ch:1:sloid:minor-end', 'match_method': 'uic_platform'},
        {'stop_id': '8501000:0:main-a', 'sloid': 'ch:1:sloid:main-a', 'match_method': 'uic_platform'},
        {'stop_id': '8501000:0:main-b', 'sloid': 'ch:1:sloid:main-b', 'match_method': 'uic_platform'},
        {'stop_id': '8502000:0:main', 'sloid': 'ch:1:sloid:main-end', 'match_method': 'uic_platform'},
    ])

    itineraries_df, stop_calls_df = _build_itinerary_frames(gtfs_data, integrated)

    assert len(itineraries_df) == 1
    assert len(stop_calls_df) == 2
    assert itineraries_df.iloc[0]['direction_label'] == 'Alpha -> Beta'

