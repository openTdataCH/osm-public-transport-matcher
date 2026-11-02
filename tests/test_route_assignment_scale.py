from osm_public_transport_matcher.routes import _choose_best_itinerary_pairs


def test_choose_best_itinerary_pairs_handles_large_source_small_osm_without_recursion_error():
    source_itineraries = [
        {'id': itinerary_id}
        for itinerary_id in range(1, 1501)
    ]
    osm_itineraries = [
        {'id': 10_001},
        {'id': 10_002},
    ]
    eligible = {(5, 10_001): (9, 0.9), (999, 10_002): (8, 0.8)}
    pair_scores = {}
    for source_itinerary in source_itineraries:
        for osm_itinerary in osm_itineraries:
            matched, score = eligible.get((source_itinerary['id'], osm_itinerary['id']), (0, 0.0))
            pair_scores[(source_itinerary['id'], osm_itinerary['id'])] = {
                'is_eligible': matched > 0,
                'matched_stop_count': matched,
                'stop_score': score,
            }

    chosen_pairs = _choose_best_itinerary_pairs(source_itineraries, osm_itineraries, pair_scores)

    assert len(chosen_pairs) == 2
    assert {(source_row['id'], osm_row['id']) for source_row, osm_row, _ in chosen_pairs} == {
        (5, 10_001),
        (999, 10_002),
    }
