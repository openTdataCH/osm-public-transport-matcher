from osm_public_transport_matcher.routes import (
    _build_itinerary_rows,
    _choose_best_itinerary_pairs,
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
    assert score_row['is_eligible'] is True
    assert score_row['match_reason'] == 'ordered_stop_match'


def test_score_itinerary_pair_counts_the_station_with_or_without_resolved_platforms():
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

    # Another platform of the same station still serves it; another station does not.
    resolved_elsewhere = [{**source_calls[0], 'source_stop_key': 'atlas:other'}]
    osm_resolved = [{**osm_calls[0], 'source_stop_key': 'atlas:this'}]
    other_platform = _score_itinerary_pair(source_itinerary, osm_itinerary, resolved_elsewhere, osm_resolved)
    assert (other_platform['matched_stop_count'], other_platform['matched_platform_count']) == (1, 0)
    other_station = [{**osm_resolved[0], 'station_ref': '8500200'}]
    assert _score_itinerary_pair(source_itinerary, osm_itinerary, resolved_elsewhere, other_station)['matched_stop_count'] == 0


def _calls(*stops):
    """Stop calls from (station, platform) pairs; the platform is the resolved source stop."""
    return [{'source_stop_key': f'atlas:{station}:{platform}', 'station_ref': station} for station, platform in stops]


def _pair(source_itineraries, osm_itineraries, calls):
    """Pair as the profiles do: an unknown direction does not refuse a pair."""
    scores = {(source['id'], osm['id']): _score_itinerary_pair(source, osm, calls[source['id']], calls[osm['id']],
                                                               require_direction_match=False)
              for source in source_itineraries for osm in osm_itineraries}
    return [(source['id'], osm['id']) for source, osm, _ in
            _choose_best_itinerary_pairs(source_itineraries, osm_itineraries, scores)]


def test_an_itinerary_pairs_with_the_variant_ending_where_it_ends_whatever_the_platform():
    # S10 at Zürich Selnau (station 90): trains running on to Zürich HB (88)
    # call at platform 1, trains ending at Selnau at platform 2. The OSM
    # relation ends at Selnau, on the stop position matched to platform 1.
    line = [(57, 0), (56, 0), (55, 0), (54, 0), (53, 0), (52, 0), (51, 0)]
    calls = {1: _calls(*line, (90, 1), (88, 22)), 2: _calls(*line, (90, 2)), 10: _calls(*line, (90, 1))}
    sources = [{'id': 1, 'direction_id': '0'}, {'id': 2, 'direction_id': '0'}]

    assert _pair(sources, [{'id': 10, 'direction_id': '0'}], calls) == [(2, 10)]


def test_the_variant_with_the_same_ends_wins_over_one_sharing_more_stops():
    # Bus 791 Schwägalp -> Urnäsch: the variant running on to St. Gallen shares
    # 12 of 14 stops, the one ending at Urnäsch 11 of 12; the terminus decides.
    osm_stops = [(station, 0) for station in range(1, 13)]
    calls = {
        1: _calls(*osm_stops, (13, 0), (14, 0)),
        2: _calls(*osm_stops[:10], (99, 0), osm_stops[11]),
        10: _calls(*osm_stops),
    }
    sources = [{'id': 1, 'direction_id': '0'}, {'id': 2, 'direction_id': '0'}]

    assert _pair(sources, [{'id': 10, 'direction_id': '0'}], calls) == [(2, 10)]


def test_shared_platforms_break_a_tie_between_variants_serving_the_same_stations():
    calls = {1: _calls((1, 2), (2, 2), (3, 2)), 2: _calls((1, 1), (2, 1), (3, 1)), 10: _calls((1, 1), (2, 1), (3, 2))}
    sources = [{'id': 1, 'direction_id': '0'}, {'id': 2, 'direction_id': '0'}]

    assert _pair(sources, [{'id': 10, 'direction_id': '0'}], calls) == [(2, 10)]


def test_a_relation_without_a_direction_pairs_with_the_itinerary_run_its_way():
    # IR 46: neither relation has ref_trips. Each one has the stops of one
    # source itinerary in order, and those of the other in reverse.
    outward = [(station, 0) for station in range(1, 7)]
    calls = {1: _calls(*outward), 2: _calls(*reversed(outward)),
             10: _calls(*outward), 11: _calls(*reversed(outward))}
    sources = [{'id': 1, 'direction_id': '0'}, {'id': 2, 'direction_id': '1'}]
    relations = [{'id': 10, 'direction_id': None}, {'id': 11, 'direction_id': None}]

    assert _pair(sources, relations, calls) == [(1, 10), (2, 11)]
    score = _score_itinerary_pair(sources[0], relations[0], calls[1], calls[10], require_direction_match=False)
    assert score['match_reason'] == 'ordered_stop_match_direction_unknown'
    # A profile that requires the direction refuses the same pair.
    required = _score_itinerary_pair(sources[0], relations[0], calls[1], calls[10])
    assert required['match_reason'] == 'direction_mismatch' and not required['is_eligible']


def test_two_known_different_directions_are_never_paired():
    stops = [(station, 0) for station in range(1, 7)]
    calls = {1: _calls(*stops), 10: _calls(*stops)}

    assert _pair([{'id': 1, 'direction_id': '0'}], [{'id': 10, 'direction_id': '1'}], calls) == []


def test_a_pair_with_equal_directions_comes_before_one_with_an_unknown_direction():
    # S36 Bülach -> Koblenz: the itineraries to Koblenz and to Waldshut have the
    # same stops in Switzerland. The relation with ref_trips keeps the partner
    # it has when the direction is required; the one without takes the other.
    stops = [(station, 0) for station in range(1, 7)]
    calls = {1: _calls(*stops), 2: _calls(*stops), 10: _calls(*stops), 11: _calls(*stops)}
    sources = [{'id': 1, 'direction_id': '0'}, {'id': 2, 'direction_id': '0'}]
    relations = [{'id': 10, 'direction_id': None}, {'id': 11, 'direction_id': '0'}]

    assert _pair(sources, relations, calls) == [(1, 11), (2, 10)]


def test_the_swiss_profile_pairs_relations_without_a_direction():
    from osm_public_transport_matcher.profiles import SWITZERLAND

    assert SWITZERLAND.itinerary_require_direction is False


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


def test_indexed_route_id_candidates_equal_exhaustive_comparison():
    import random
    from osm_public_transport_matcher.routes import _lines_named_by_route_id, _route_id_kind
    rng = random.Random(173)
    source = [dict(id=i, side='source', gtfs_route_id=rng.choice(['R1', 'R2', 'R3', None]),
                   normalized_route_id=rng.choice(['N1', 'N2', None])) for i in range(1, 31)]
    osm = [dict(id=i, side='osm', gtfs_route_id=rng.choice(['R1', 'R2', 'R3', None]),
                normalized_route_id=rng.choice(['N1', 'N2', None]),
                display_route_id=rng.choice(['R1', 'R3', None]))
           for i in range(40, 90)]
    exhaustive = {(a['id'], o['id']): _route_id_kind(a, o) for a in source for o in osm if _route_id_kind(a, o)}
    assert _lines_named_by_route_id(source, osm) == exhaustive
    assert set(exhaustive.values()) == {'exact_gtfs_route_id', 'normalized_gtfs_route_id', 'display_route_id_match'}


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

    # The resolved member stands for its stop, also when the two members were
    # matched to different platforms of the station (6 and 7); repeated
    # platforms stay separate calls.
    assert [call['stop_sequence'] for call in calls] == [0, 3, 4, 5, 6]

    source_calls = [{'source_stop_key': key, 'station_ref': None} for key in ('atlas:A', 'atlas:B')]
    osm_calls = [{'source_stop_key': call['resolved_source_key'], 'station_ref': call['station_ref']}
                 for call in _one_call_per_stop_visit([member(0, 'stop', 'atlas:A'), member(1, 'platform', 'atlas:A'),
                                                       member(2, 'stop', 'atlas:B', '1'), member(3, 'platform', 'atlas:B', '1')])]
    score = _score_itinerary_pair({'direction_id': '0'}, {'direction_id': '0'}, source_calls, osm_calls)
    assert score['stop_score'] == 1.0 and score['is_eligible']


def _links_of(gtfs_lines, osm_lines, **options):
    """Run compare_routes on a small network and return its links as {(GTFS route ID, OSM line, match method)}.

    ``gtfs_lines`` holds (route_id, short_name, route_type, itineraries) and
    ``osm_lines`` holds (name, ref, route, gtfs_route_id, stops); every OSM line
    is a route master with one relation. Stop ``X`` is the source stop
    ``atlas:X`` and the OSM node ``nX``, which stop matching paired.
    """
    from types import SimpleNamespace

    import pandas as pd

    from osm_public_transport_matcher.core.models import MatchRecord, OsmNode, SourceStop
    from osm_public_transport_matcher.profiles.switzerland import normalize_route_id
    from osm_public_transport_matcher.routes import compare_routes

    stops = sorted({stop for *_, itineraries in gtfs_lines for itinerary in itineraries for stop in itinerary}
                   | {stop for *_, osm_stops in osm_lines for stop in osm_stops})
    itineraries = [(route_id, f'{route_id}:{index}', index % 2, stop_list)
                   for route_id, _, _, stop_lists in gtfs_lines for index, stop_list in enumerate(stop_lists)]
    route_data = {
        'source_routes': pd.DataFrame([
            {'route_id': route_id, 'route_short_name': short_name, 'route_type': route_type,
             'route_id_normalized': normalize_route_id(route_id)}
            for route_id, short_name, route_type, _ in gtfs_lines]),
        'source_itineraries': pd.DataFrame([
            {'itinerary_id': itinerary_id, 'route_id': route_id, 'direction_id': str(direction)}
            for route_id, itinerary_id, direction, _ in itineraries]),
        'source_stop_calls': pd.DataFrame([
            {'itinerary_id': itinerary_id, 'stop_sequence': sequence, 'source_stop_key': f'atlas:{stop}'}
            for _, itinerary_id, _, stop_list in itineraries for sequence, stop in enumerate(stop_list)]),
        'osm_route_masters': pd.DataFrame([
            {'route_master_id': name, 'route_master': route, 'ref': ref, 'gtfs_route_id': route_id,
             'route_id_normalized': normalize_route_id(route_id)}
            for name, ref, route, route_id, _ in osm_lines]),
        'osm_route_relations': pd.DataFrame([
            {'relation_id': f'r-{name}', 'route': route, 'ref': ref, 'gtfs_route_id': route_id,
             'route_id_normalized': normalize_route_id(route_id), 'route_master_id': name}
            for name, ref, route, route_id, _ in osm_lines]),
        'osm_route_relation_stops': pd.DataFrame([
            {'relation_id': f'r-{name}', 'stop_sequence': sequence, 'osm_node_id': f'n{stop}', 'stop_role': 'platform'}
            for name, *_, osm_stops in osm_lines for sequence, stop in enumerate(osm_stops)]),
    }
    matched = [MatchRecord(SourceStop('atlas', stop, 47.0, 8.0), OsmNode(f'n{stop}', 47.0, 8.0), 'test', 0.0, '')
               for stop in stops]
    base_data = SimpleNamespace(matched=matched, unmatched_source=[], unmatched_osm=[], all_osm_nodes=[], disused_osm=[])
    routes = compare_routes(route_data, {f'atlas:{stop}' for stop in stops}, base_data, **options)
    families = {row['id']: row for row in routes['line_families']}
    return {(families[row['source_line_family_id']]['key'], families[row['osm_line_family_id']]['key'], row['match_method'])
            for row in routes['line_family_matches']}


def test_line_numbers_compare_without_case_and_spaces_and_with_the_profile_alias():
    from osm_public_transport_matcher.routes import _line_number_keys
    from osm_public_transport_matcher.swiss import timetable_line_number

    assert _line_number_keys('IR 26') == _line_number_keys('ir26') == frozenset({'ir26'})
    assert _line_number_keys(None) == frozenset()
    assert not _line_number_keys('31.158') & _line_number_keys('158')
    assert _line_number_keys('31.158', timetable_line_number) & _line_number_keys('158', timetable_line_number)
    assert _line_number_keys('31.060', timetable_line_number) >= {'60'}
    # A cableway section such as 2587.1 is not a timetable number of a bus line.
    assert timetable_line_number('2587.1') is None


def test_line_modes_follow_the_osm_route_and_the_gtfs_route_type():
    from osm_public_transport_matcher.routes import _line_mode

    def osm(route):
        return _line_mode({'side': 'osm', 'route_type': route})

    def source(route_type):
        return _line_mode({'side': 'source', 'route_type': route_type})

    assert (osm('trolleybus'), osm('light_rail'), osm('train'), osm('piste'), osm(None)) == ('bus', 'tram', 'rail', None, None)
    assert (source('3'), source('700'), source('109'), source('900'), source('401'), source('1300')) == (
        'bus', 'bus', 'rail', 'tram', 'metro', 'aerialway')
    assert (source('1500'), source('x'), source(None)) == (None, None, None)


BERN = ['B1', 'B2', 'B3', 'B4', 'B5']
THUN = ['T1', 'T2', 'T3', 'T4', 'T5']


BY_STOPS = 'line_number_and_stops'
EXACT, YEAR_IGNORED = 'exact_gtfs_route_id', 'normalized_gtfs_route_id'


def test_link_lines_links_every_candidate_whose_stops_agree_else_the_route_id_alone():
    from osm_public_transport_matcher.routes import _LineCandidate, _link_lines

    def row(source_id, osm_id, route_id=None, same_number=True, stop_score=0.0, shares_a_stop=True):
        return _LineCandidate(source_id, osm_id, route_id, same_number, stop_score, shares_a_stop)

    def linked(*rows, **options):
        return [(link.source_id, link.osm_id) for link in _link_lines(rows, **options)]

    # Stops that agree link on both sides: two routes of one OSM line, two OSM lines of one route.
    assert linked(row(1, 10, stop_score=1.0), row(2, 10, stop_score=0.8), row(2, 11, stop_score=0.9)) == [
        (1, 10), (2, 10), (2, 11)]
    assert linked(row(1, 10, stop_score=0.79)) == []
    assert linked(row(1, 10, stop_score=0.85), min_stop_ratio=0.9) == []
    # A route ID whose stops do not agree is left out when the stops find a line,
    # and links alone when they find none (IR 26: route D by its ID, route C by its stops).
    assert linked(row(1, 10, EXACT, stop_score=0.5), row(1, 11, stop_score=0.8)) == [(1, 11)]
    assert linked(row(1, 10, EXACT, stop_score=0.14), row(2, 10, stop_score=1.0)) == [(1, 10), (2, 10)]
    # A route ID alone needs a shared stop and, with the year ignored, the same number.
    assert linked(row(1, 10, EXACT, shares_a_stop=False)) == []
    assert linked(row(1, 10, EXACT, same_number=False)) == [(1, 10)]
    assert linked(row(1, 10, YEAR_IGNORED, same_number=False)) == []
    assert linked(row(1, 10, YEAR_IGNORED)) == [(1, 10)]
    # Several OSM lines carry the ID: the same number first, then the exact ID, then the stops by 0.1.
    assert linked(row(1, 10, EXACT, same_number=False, stop_score=0.7), row(1, 11, YEAR_IGNORED, stop_score=0.3)) == [(1, 11)]
    assert linked(row(1, 10, YEAR_IGNORED, stop_score=0.7), row(1, 11, EXACT, stop_score=0.3)) == [(1, 11)]
    assert linked(row(1, 10, EXACT, stop_score=0.5), row(1, 11, EXACT, stop_score=0.3)) == [(1, 10)]
    assert linked(row(1, 10, EXACT, stop_score=0.5), row(1, 11, EXACT, stop_score=0.45)) == []


def test_lines_that_share_stations_but_not_platforms_have_the_itinerary_score():
    from osm_public_transport_matcher.routes import _line_candidates, _link_lines

    # IC 3: the trains stop at other tracks than the OSM stop positions were matched to,
    # and two OSM calls are known by their station only.
    stations = ['8500010', '8500218', '8503000', '8509000', '8509002']
    calls = {
        1: [{'source_stop_key': f'atlas:{station}:3', 'station_ref': station} for station in stations],
        2: [{'source_stop_key': f'atlas:{station}:7' if index < 3 else None, 'station_ref': station}
            for index, station in enumerate(stations)],
    }
    itineraries = {1: [{'id': 1, 'direction_id': '0'}], 2: [{'id': 2, 'direction_id': None}]}
    gtfs = {'id': 1, 'side': 'source', 'route_type': '102', 'ref': 'IC3', 'gtfs_route_id': '91-3-C-j26-1'}

    def candidates(osm_line):
        return _line_candidates([gtfs], [osm_line], itineraries, calls)

    [candidate] = candidates({'id': 2, 'side': 'osm', 'route_type': 'train', 'ref': 'IC 3'})
    assert (candidate.route_id, candidate.same_number, candidate.stop_score, candidate.shares_a_stop) == (None, True, 1.0, True)
    assert candidate.match_method == BY_STOPS and _link_lines([candidate]) == [candidate]
    # Another number, another mode or a line marked non-GTFS is no candidate.
    assert candidates({'id': 2, 'side': 'osm', 'route_type': 'train', 'ref': 'IC 30'}) == []
    assert candidates({'id': 2, 'side': 'osm', 'route_type': 'bus', 'ref': 'IC 3'}) == []
    assert candidates({'id': 2, 'side': 'osm', 'route_type': 'train', 'ref': 'IC 3', 'is_non_gtfs': True}) == []


def test_year_ignored_route_id_of_a_line_elsewhere_is_refused_and_the_stops_find_the_line():
    links = _links_of(
        [('92-21-B-j26-1', '21', '700', [THUN, THUN[::-1]]), ('92-21-j26-1', '21', '700', [BERN, BERN[::-1]])],
        # Bern's bus 21 still carries the 2021 ID, which now belongs to Thun's bus 21.
        [('bern-21', '21', 'bus', '92-21-B-j21-1', BERN)],
    )
    assert links == {('92-21-j26-1', 'bern-21', BY_STOPS)}


def test_a_route_id_alone_needs_a_shared_stop_and_with_the_year_ignored_the_same_number():
    route = '92-36-D-j26-1'
    gtfs = [(route, '36', '700', [BERN])]
    # One shared stop of six: the stops do not agree, and the ID links the lines.
    assert _links_of(gtfs, [('a', '36', 'bus', '92-36-D-j22-1', BERN[:1] + THUN)]) == {(route, 'a', YEAR_IGNORED)}
    assert _links_of(gtfs, [('a', '92', 'bus', '92-36-D-j22-1', BERN[:1] + THUN)]) == set()
    assert _links_of(gtfs, [('a', '36', 'bus', '92-36-D-j22-1', THUN)]) == set()
    # An exact ID needs a shared stop too, but not the number.
    assert _links_of(gtfs, [('a', '92', 'bus', route, BERN[:1] + THUN)]) == {(route, 'a', EXACT)}
    assert _links_of(gtfs, [('a', '36', 'bus', route, THUN)]) == set()
    # With stops that agree, the line the ID names is linked whatever its number.
    assert _links_of(gtfs, [('a', '92', 'bus', '92-36-D-j22-1', BERN)]) == {(route, 'a', YEAR_IGNORED)}
    # A refused ID does not block the OSM line with the same ID that runs there.
    links = _links_of(gtfs, [('a', '36', 'bus', '92-36-D-j21-1', THUN), ('b', '36', 'bus', '92-36-D-j24-1', BERN)])
    assert links == {(route, 'b', YEAR_IGNORED)}


def test_an_osm_line_with_a_route_id_but_not_its_stops_is_left_out_when_the_stops_find_a_line():
    # Buses 521 and 523: both OSM lines carry the route ID of GTFS bus 521.
    route = '96-729-j26-1'
    gtfs = [(route, '521', '700', [BERN])]
    links = _links_of(gtfs, [('osm-523', '523', 'bus', route, BERN[:3] + THUN[:2]), ('osm-521', '521', 'bus', route, BERN)])
    assert links == {(route, 'osm-521', EXACT)}
    # Both have the stops: both are linked, also the one with another number.
    links = _links_of(gtfs, [('osm-522', '522', 'bus', route, BERN), ('osm-521', '521', 'bus', route, BERN)])
    assert links == {(route, 'osm-522', EXACT), (route, 'osm-521', EXACT)}


def test_of_two_osm_lines_with_one_route_id_and_no_agreeing_stops_the_number_then_the_stops_decide():
    route = '96-729-j26-1'
    gtfs = [(route, '521', '700', [BERN + THUN])]
    # The number counts before the stops: 7 of 10 stops under another number lose to 3 of 10.
    links = _links_of(gtfs, [('other-number', '523', 'bus', route, BERN + THUN[:2]), ('same-number', '521', 'bus', route, BERN[:3])])
    assert links == {(route, 'same-number', EXACT)}
    links = _links_of(gtfs, [('part', '521', 'bus', route, BERN[:3]), ('more', '521', 'bus', route, BERN + THUN[:1])])
    assert links == {(route, 'more', EXACT)}
    assert _links_of(gtfs, [('a', '521', 'bus', route, BERN[:3]), ('b', '521', 'bus', route, BERN[:3])]) == set()


def test_lines_without_a_route_id_need_the_same_number_and_mode():
    gtfs = [('92-3-j26-1', '3', '700', [BERN]), ('92-N3-j26-1', 'N3', '705', [BERN]), ('91-9-j26-1', '9', '900', [THUN])]
    links = _links_of(gtfs, [('night', 'N 3', 'bus', None, BERN), ('day', '3', 'bus', None, BERN),
                             ('bus-9', '9', 'bus', None, THUN), ('no-ref', None, 'bus', None, THUN)])
    assert links == {('92-N3-j26-1', 'night', BY_STOPS), ('92-3-j26-1', 'day', BY_STOPS)}


def test_the_stops_agree_at_the_itinerary_ratio_with_two_shared_stops():
    gtfs = [('92-5-j26-1', '5', '700', [BERN[:3]]), ('92-6-j26-1', '6', '700', [BERN + THUN])]
    # 7 of 10 stops in order is a ratio of 0.7.
    assert _links_of(gtfs, [('partial', '6', 'bus', None, BERN + THUN[:2])]) == set()
    assert _links_of(gtfs, [('partial', '6', 'bus', None, BERN + THUN[:3])]) == {('92-6-j26-1', 'partial', BY_STOPS)}
    assert _links_of(gtfs, [('partial', '6', 'bus', None, BERN + THUN[:3])], min_stop_ratio=0.9) == set()
    # A line of three stops shares all three; one shared stop shows no order.
    assert _links_of(gtfs, [('short', '5', 'bus', None, BERN[:3])]) == {('92-5-j26-1', 'short', BY_STOPS)}
    assert _links_of([('92-1-j26-1', '1', '700', [BERN[:1]])], [('one', '1', 'bus', None, BERN[:1])]) == set()


def test_a_two_station_cableway_is_linked_by_its_number_and_both_stations():
    # PB 2586 carries a route ID that is not in the timetable; its two stations are its only stops.
    gtfs = [('93-258-6-j26-1', '2586', '1300', [['L1', 'L2'], ['L2', 'L1']])]
    assert _links_of(gtfs, [('pb-2586', '2586', 'aerialway', '93-8J-Y-j26-1', ['L1', 'L2'])]) == {
        ('93-258-6-j26-1', 'pb-2586', BY_STOPS)}
    assert _links_of(gtfs, [('pb-2586', '2586', 'aerialway', None, ['L1', 'L3'])]) == set()
    assert _links_of(gtfs, [('pb-2586', '2586', 'funicular', None, ['L1', 'L2'])]) == set()


def test_a_source_call_takes_its_resolved_stops_station_else_the_feeds():
    from osm_public_transport_matcher.routes import _build_source_stop_call_row

    lookup = {'atlas:szu': {'station_ref': '8503088'}}
    # The feed files the SZU platform under Zürich HB; ATLAS has it at Zürich HB SZU.
    resolved, _ = _build_source_stop_call_row(
        {'itinerary_id': 'i', 'source_stop_key': 'atlas:szu', 'station_ref': '8503000'}, {'atlas:szu'}, lookup)
    station_only, _ = _build_source_stop_call_row(
        {'itinerary_id': 'i', 'source_stop_id': 'ch:1:sloid:1075', 'station_ref': '8501075'}, {'atlas:szu'}, lookup)
    assert (resolved['station_ref'], station_only['station_ref']) == ('8503088', '8501075')
    assert station_only['source_stop_key'] is None


def test_a_line_takes_every_line_whose_stops_agree():
    # Two GTFS route IDs for one line 400 on the same stops: the OSM line takes both.
    gtfs = [('92-400-j26-1', '400', '700', [BERN]), ('92-400-A-j26-1', '400', '700', [BERN])]
    assert _links_of(gtfs, [('a', '400', 'bus', None, BERN)]) == {
        ('92-400-j26-1', 'a', BY_STOPS), ('92-400-A-j26-1', 'a', BY_STOPS)}
    # A route that shares 4 of 7 stops stays out.
    gtfs = [('92-400-j26-1', '400', '700', [BERN]), ('92-400-A-j26-1', '400', '700', [BERN[:4] + THUN[:3]])]
    assert _links_of(gtfs, [('a', '400', 'bus', None, BERN)]) == {('92-400-j26-1', 'a', BY_STOPS)}
    # One line mapped twice in OSM: the GTFS route takes both, the tagged one by its ID.
    links = _links_of(gtfs[:1], [('tagged', '400', 'bus', '92-400-j26-1', BERN), ('copy', '400', 'bus', None, BERN)])
    assert links == {('92-400-j26-1', 'tagged', EXACT), ('92-400-j26-1', 'copy', BY_STOPS)}


def test_a_route_id_and_the_stops_of_another_route_link_one_osm_line_to_both():
    # IR 26: the OSM tag names the route of a short section; another route has all the stops.
    gtfs = [('91-26-C-j26-1', 'IR26', '102', [BERN + THUN]),
            ('91-26-D-j26-1', 'IR26', '102', [BERN[:2] + ['X1', 'X2', 'X3']])]
    links = _links_of(gtfs, [('ir-26', 'IR 26', 'train', '91-26-D-j26-1', BERN + THUN)])
    assert links == {('91-26-C-j26-1', 'ir-26', BY_STOPS), ('91-26-D-j26-1', 'ir-26', EXACT)}


def test_itineraries_are_paired_inside_every_link_of_a_line():
    from osm_public_transport_matcher.routes import _choose_best_itinerary_pairs, _line_candidates, _link_lines

    calls = {itinerary: [{'source_stop_key': f'atlas:{stop}', 'station_ref': None} for stop in BERN] for itinerary in (1, 2, 3)}
    itineraries = {1: [{'id': 1, 'direction_id': '0'}], 2: [{'id': 2, 'direction_id': '0'}], 3: [{'id': 3, 'direction_id': None}]}
    twins = [{'id': family, 'side': 'source', 'route_type': '700', 'ref': '400'} for family in (1, 2)]
    links = _link_lines(_line_candidates(twins, [{'id': 3, 'side': 'osm', 'route_type': 'bus', 'ref': '400'}], itineraries, calls,
                                         require_direction_match=False))
    pairs = [(source['id'], osm['id']) for link in links
             for source, osm, _ in _choose_best_itinerary_pairs(itineraries[link.source_id], itineraries[link.osm_id], link.scores)]
    # The one relation is the itinerary of both twin routes.
    assert pairs == [(1, 3), (2, 3)]
