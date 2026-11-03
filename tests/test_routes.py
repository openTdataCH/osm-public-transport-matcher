from osm_public_transport_matcher.routes import (
    _build_itinerary_rows,
    _count_stop_sequence_matches,
    _one_call_per_stop_visit,
    _resolve_osm_stop_fields,
    _score_itinerary_pair,
)


def test_resolve_osm_stop_fields_prefers_stop_match_over_raw_uic_key():
    resolved = _resolve_osm_stop_fields(
        'osm-1',
        {
            'uic_ref': '8500001',
            'canonical_stop_key': 'uic:8500001',
            'stop_label': 'Stop A',
        },
        {
            'osm_node_lookup': {},
            'matched_osm_to_source_key': {'osm-1': 'atlas:ch:1:sloid:8500001:1'},
            'unique_source_key_by_station_ref': {'8500001': 'atlas:ch:1:sloid:8500001:9'},
            'source_stop_lookup': {},
        },
    )

    assert resolved['canonical_stop_key'] == 'atlas:ch:1:sloid:8500001:1'
    assert resolved['station_ref'] == '8500001'


def test_stop_sequence_matches_count_variant_keys_and_keep_order():
    source_calls = [
        {
            'source_stop_key': 'atlas:A:1',
            'source_stop_key_variants': '["atlas:A:1", "atlas:A:2"]',
            'canonical_stop_key': 'atlas:A:1',
            'station_ref': '8500001',
        },
        {
            'source_stop_key': 'atlas:B:1',
            'source_stop_key_variants': None,
            'canonical_stop_key': 'atlas:B:1',
            'station_ref': '8500002',
        },
        {
            'source_stop_key': 'atlas:C:1',
            'source_stop_key_variants': None,
            'canonical_stop_key': 'atlas:C:1',
            'station_ref': '8500003',
        },
    ]
    osm_calls = [
        {'canonical_stop_key': 'atlas:A:2', 'source_stop_key': 'atlas:A:2', 'station_ref': '8500001'},
        {'canonical_stop_key': 'osm:unmatched', 'station_ref': '8500009'},
        {'canonical_stop_key': 'atlas:C:1', 'source_stop_key': 'atlas:C:1', 'station_ref': '8500003'},
    ]

    assert _count_stop_sequence_matches(source_calls, osm_calls) == 2
    assert _count_stop_sequence_matches(source_calls, list(reversed(osm_calls))) == 1


def test_score_itinerary_pair_requires_exact_direction_and_80_percent_stop_ratio():
    source_itinerary = {'direction_id': '0'}
    osm_itinerary = {'direction_id': '0'}
    source_calls = [
        {'source_stop_key': f'atlas:{index}', 'source_stop_key_variants': None,
         'canonical_stop_key': f'atlas:{index}', 'station_ref': f'850000{index}'}
        for index in range(1, 6)
    ]
    osm_calls = [
        {'source_stop_key': f'atlas:{index}', 'canonical_stop_key': f'atlas:{index}', 'station_ref': f'850000{index}'}
        for index in range(1, 5)
    ] + [{'canonical_stop_key': 'osm:unmatched', 'station_ref': '8509999'}]

    score_row = _score_itinerary_pair(source_itinerary, osm_itinerary, source_calls, osm_calls)

    assert score_row['matched_stop_count'] == 4
    assert score_row['stop_score'] == 0.8
    assert score_row['direction_score'] == 1.0
    assert score_row['is_eligible'] is True
    assert score_row['match_reason'] == 'ordered_stop_match'


def test_score_itinerary_pair_uses_station_reference_only_without_resolved_identity():
    source_itinerary = {'direction_id': '1'}
    osm_itinerary = {'direction_id': '1'}
    source_calls = [{
        'source_stop_key': None,
        'source_stop_key_variants': None,
        'canonical_stop_key': 'stop-1',
        'station_ref': '8500100',
    }]
    osm_calls = [{'canonical_stop_key': 'uic:8500100', 'station_ref': '8500100'}]

    score_row = _score_itinerary_pair(source_itinerary, osm_itinerary, source_calls, osm_calls)

    assert score_row['matched_stop_count'] == 1
    assert score_row['is_eligible'] is True

    resolved_elsewhere = [{**source_calls[0], 'source_stop_key': 'atlas:other'}]
    osm_resolved = [{**osm_calls[0], 'source_stop_key': 'atlas:this'}]
    assert _score_itinerary_pair(source_itinerary, osm_itinerary, resolved_elsewhere, osm_resolved)['matched_stop_count'] == 0


def test_build_itinerary_rows_prefers_osm_from_to_for_display_and_to_for_headsign():
    itinerary_rows, _, _ = _build_itinerary_rows(
        {
            'itineraries': [],
            'stop_calls': [],
        },
        {
            'osm_route_relations': [
                {
                    'relation_id': '300',
                    'name': 'Bus 31: Zurich HB -> Kienastenwies',
                    'from_name': 'Zurich HB',
                    'to_name': 'Kienastenwies',
                    'ref': '31',
                    'gtfs_shape_id': None,
                }
            ],
            'osm_route_relation_stops': [
                {
                    'relation_id': '300',
                    'stop_sequence': 1,
                    'direction_id': '0',
                    'canonical_stop_key': 'osm:100',
                    'osm_node_id': '100',
                    'stop_role': 'platform',
                    'stop_label': 'Zurich HB',
                    'station_ref': '8503000',
                    'stop_lat': None,
                    'stop_lon': None,
                }
            ],
        },
        {},
        {'300': 7},
    )

    osm_itinerary = itinerary_rows[0]

    assert osm_itinerary['side'] == 'osm'
    assert osm_itinerary['key'] == '300'
    assert osm_itinerary['display_name'] == 'Zurich HB -> Kienastenwies'
    assert osm_itinerary['representative_headsign'] == 'Kienastenwies'


def test_indexed_family_candidates_equal_exhaustive_scoring():
    import random
    from osm_public_transport_matcher.routes import _candidate_line_family_pairs, _score_line_family_pair
    rng = random.Random(173)
    source = [dict(id=i, side='source', gtfs_route_id=rng.choice(['R1', 'R2', 'R3', None]),
                   normalized_route_id=rng.choice(['N1', 'N2', None])) for i in range(1, 31)]
    osm = [dict(id=i, side='osm', gtfs_route_id=rng.choice(['R1', 'R2', 'R3', None]),
                normalized_route_id=rng.choice(['N1', 'N2', None]),
                display_route_id=rng.choice(['R1', 'R3', None]), is_non_gtfs=i % 7 == 0)
           for i in range(40, 90)]
    exhaustive = []
    for a in source:
        for o in osm:
            score, reason = _score_line_family_pair(a, o)
            if score and not o['is_non_gtfs']:
                exhaustive.append((score, a['id'], o['id'], reason))
    indexed = [(score, a['id'], o['id'], reason) for score, a, o, reason in _candidate_line_family_pairs(source, osm)]
    assert sorted(indexed) == sorted(exhaustive)


def test_stop_sequence_matches_equal_exhaustive_longest_common_subsequence():
    import random
    from functools import lru_cache
    from osm_public_transport_matcher.routes import _same_stop, _stop_identity
    rng = random.Random(93)
    pool = [{'source_stop_key': 'a', 'station_ref': '1'}, {'source_stop_key': 'b', 'station_ref': '1'},
            {'source_stop_key_variants': '["a", "c"]'}, {'station_ref': '1'}, {}, {'station_ref': '2'}]
    for _ in range(100):
        a = rng.choices(pool, k=rng.randrange(12))
        b = rng.choices(pool, k=rng.randrange(12))

        @lru_cache(maxsize=None)
        def lcs(i, j):
            if i == len(a) or j == len(b):
                return 0
            best = max(lcs(i + 1, j), lcs(i, j + 1))
            if _same_stop(_stop_identity(a[i]), _stop_identity(b[j])):
                best = max(best, 1 + lcs(i + 1, j + 1))
            return best

        assert _count_stop_sequence_matches(a, b) == lcs(0, 0)


def test_stop_position_and_platform_of_one_stop_make_one_call():
    def member(sequence, role, key, station='8591122'):
        return {'relation_id': '1', 'stop_sequence': sequence, 'osm_node_id': f'n{sequence}', 'stop_role': role,
                'resolved_source_key': key, 'station_ref': station}

    calls = _one_call_per_stop_visit([
        member(0, 'stop', 'atlas:A'),
        member(1, 'platform', 'atlas:A'),
        member(2, 'stop_entry_only', None, '8591249'),
        member(3, 'platform_entry_only', 'atlas:B', '8591249'),
        member(4, 'platform', 'atlas:C', '8591250'),
        member(5, 'platform', 'atlas:C', '8591250'),
        member(6, 'stop', 'atlas:D', '8591338'),
        member(7, 'platform', 'atlas:E', '8591338'),
    ])

    # The resolved member stands for its stop; repeated platforms and
    # members resolved to different source stops stay separate calls.
    assert [call['stop_sequence'] for call in calls] == [0, 3, 4, 5, 6, 7]

    source_calls = [{'source_stop_key': key, 'station_ref': None} for key in ('atlas:A', 'atlas:B')]
    osm_calls = [{'source_stop_key': call['resolved_source_key'], 'station_ref': call['station_ref']}
                 for call in _one_call_per_stop_visit([member(0, 'stop', 'atlas:A'), member(1, 'platform', 'atlas:A'),
                                                       member(2, 'stop', 'atlas:B', '1'), member(3, 'platform', 'atlas:B', '1')])]
    score = _score_itinerary_pair({'direction_id': '0'}, {'direction_id': '0'}, source_calls, osm_calls)
    assert score['stop_score'] == 1.0 and score['is_eligible']
