"""Identifier matching: OSM elements that name their exact source stop, such as ref:sloid."""
from osm_public_transport_matcher.adapters.atlas import atlas_state
from osm_public_transport_matcher.adapters.osm import read_osm
from osm_public_transport_matcher.api import match
from osm_public_transport_matcher.core.identifiers import IdentifierIndex
from osm_public_transport_matcher.core.models import OsmNode, Reference, SourceStop
from osm_public_transport_matcher.profiles import SWITZERLAND
from osm_public_transport_matcher.results import validate_bundle, write_bundle

RULE = SWITZERLAND.identifier_rules[0]


def atlas(sloid, lat, lon, code='', uic=None):
    uic = uic or '85' + sloid.split(':')[3].zfill(5)
    return SourceStop('atlas', sloid, lat, lon, name='Stop', platform_code=code, station_ref=uic,
                      references=(Reference('sloid', sloid), Reference('uic', uic, 'station')))


def osm(node_id, lat, lon, way=False, **tags):
    tags = {key.replace('__', ':'): value for key, value in tags.items()}
    return OsmNode(node_id, lat, lon, tags=tags, element_type='way' if way else 'node', identifier_only=way)


def links(output):
    return {(row.source_node.source_id, row.osm_node.node_id, row.match_type) for row in output.matched}


def identifier_problems(output):
    return {(problem.osm_ids, problem.priority) for problem in output.problems if problem.problem_type == 'identifier'}


def test_sloid_values_are_split_trimmed_deduplicated_and_checked():
    tags = {'ref:sloid': 'ch:1:sloid:1:0:1; ch:1:sloid:1:0:2', 'railway:ref:sloid': 'ch:1:sloid:1:0:1'}
    assert RULE.values(tags) == ('ch:1:sloid:1:0:1', 'ch:1:sloid:1:0:2')
    good = ('ch:1:sloid:7000', 'ch:1:sloid:7000:4', 'ch:1:sloid:7000:4:7', 'ch:1:sloid:211::161536', 'ch:1:sloid:79089:0:A')
    bad = ('8503000', 'ch:1:sloid:87357:0:989:ch:1:sloid:87357:0:3142', 'ch:23005:826:3:51', 'CH:1:SLOID:7000', 'ch:1:sloid:7000:')
    assert all(RULE.is_well_formed(value) for value in good)
    assert not any(RULE.is_well_formed(value) for value in bad)


def test_service_point_and_area_values_are_known_but_name_no_single_stop():
    index = IdentifierIndex(RULE, [atlas('ch:1:sloid:7000:4:7', 47.0, 8.0)])
    for value in ('ch:1:sloid:7000', 'ch:1:sloid:7000:4'):
        reading = index.read(osm('1', 47.0, 8.0, ref__sloid=value), 150)
        assert reading.stops == () and reading.priority is None
    assert index.read(osm('1', 47.0, 8.0, ref__sloid='ch:1:sloid:70001'), 150).unknown == ('ch:1:sloid:70001',)


def test_tagged_elements_are_linked_first_even_where_positions_suggest_the_other_stop():
    # Two directions of one stop: each tagged platform lies next to the other ATLAS platform.
    first, second = atlas('ch:1:sloid:1:0:1', 47.0, 8.0), atlas('ch:1:sloid:1:0:2', 47.0, 8.0003)
    elements = [
        osm('10', 47.0, 8.0003, public_transport='platform', uic_ref=first.station_ref, ref__sloid=first.source_id),
        osm('11', 47.0, 8.0, public_transport='platform', uic_ref=first.station_ref, railway__ref__sloid=second.source_id),
    ]
    assert links(match([first, second], elements, SWITZERLAND)) == {
        (first.source_id, '10', 'sloid_identifier'), (second.source_id, '11', 'sloid_identifier'),
    }


def test_a_stop_takes_every_carrier_and_one_tag_can_name_several_stops():
    track_3, track_4 = atlas('ch:1:sloid:2:1:3', 47.0, 8.0, '3'), atlas('ch:1:sloid:2:1:4', 47.0, 8.0001, '4')
    elements = [
        osm('20', 47.0, 8.00005, public_transport='platform', ref__sloid=f'{track_3.source_id};{track_4.source_id}'),
        osm('21', 47.0, 8.0, public_transport='stop_position', tram='yes', ref__sloid=track_3.source_id),
        osm('22', 47.0, 8.0, public_transport='stop_position', bus='yes', ref__sloid=track_3.source_id),
    ]
    output = match([track_3, track_4], elements, SWITZERLAND)
    assert links(output) == {
        (track_3.source_id, '20', 'sloid_identifier'), (track_4.source_id, '20', 'sloid_identifier'),
        (track_3.source_id, '21', 'sloid_identifier'), (track_3.source_id, '22', 'sloid_identifier'),
    }
    assert output.unmatched_osm == [] and output.unmatched_source == [] and identifier_problems(output) == set()


def test_untrusted_values_are_reported_and_leave_the_element_to_the_other_rules():
    malformed_stop, unknown_stop = atlas('ch:1:sloid:3:0:1', 47.0, 8.0), atlas('ch:1:sloid:4:0:1', 47.1, 8.0)
    near_stop, far_stop = atlas('ch:1:sloid:5:0:1', 47.2, 8.0), atlas('ch:1:sloid:5:0:2', 47.21, 8.0)
    elements = [
        osm('30', 47.0, 8.0, public_transport='platform', ref__sloid='8500003'),            # a UIC number
        osm('31', 47.1, 8.0, public_transport='platform', ref__sloid='ch:1:sloid:4:0:7'),   # not in ATLAS
        osm('32', 47.2, 8.0, public_transport='platform', ref__sloid=far_stop.source_id),   # 1.1 km away
    ]
    output = match([malformed_stop, unknown_stop, near_stop, far_stop], elements, SWITZERLAND)
    assert identifier_problems(output) == {(('30',), 3), (('31',), 2), (('32',), 1)}
    assert {(source, node) for source, node, kind in links(output)} == {
        (malformed_stop.source_id, '30'), (unknown_stop.source_id, '31'), (near_stop.source_id, '32'),
    }
    assert all(kind != 'sloid_identifier' for _, _, kind in links(output))
    far = next(problem for problem in output.problems if problem.problem_type == 'identifier' and problem.priority == 1)
    assert far.evidence['far'] == [{'value': far_stop.source_id, 'source_key': far_stop.key, 'distance_m': 1111.9}]


def test_identifier_only_ways_add_links_without_taking_stops_from_other_elements(tmp_path):
    stop = atlas('ch:1:sloid:6:0:1', 47.0, 8.0)
    elements = [
        osm('60', 47.0, 8.0, public_transport='stop_position'),
        osm('61', 47.0, 8.00002, way=True, public_transport='platform', ref__sloid=stop.source_id),
        osm('62', 47.0, 8.0, way=True, public_transport='platform', ref__sloid='ch:1:sloid:6'),
        osm('63', 47.0, 8.0, way=True, public_transport='platform', ref__sloid='ch:1:sloid:6:0:8'),
    ]
    output = match([stop], elements, SWITZERLAND)
    assert {(node, kind) for _, node, kind in links(output)} == {('60', 'distance_only_candidate'), ('way_61', 'sloid_identifier')}
    # A way whose identifier names no single stop and needs no attention is not kept.
    assert sorted(node.node_id for node in output.all_osm_nodes) == ['60', 'way_61', 'way_63']
    assert [node.node_id for node in output.unmatched_osm] == ['way_63']
    assert {(p.problem_type, p.osm_ids) for p in output.problems if p.osm_ids} == {
        ('identifier', ('way_63',)), ('unmatched', ('way_63',)),
    }
    validate_bundle(write_bundle(output, tmp_path / 'run'))


def test_the_ptv2_partner_follows_a_tagged_element_unless_it_has_its_own_identifier():
    stop, other = atlas('ch:1:sloid:7:0:1', 47.0, 8.0, uic='8500007'), atlas('ch:1:sloid:7:0:2', 47.001, 8.0, uic='8500007')
    elements = [
        osm('70', 47.0, 8.0, public_transport='platform', uic_ref='8500007', ref__sloid=stop.source_id),
        osm('71', 47.0, 8.00003, public_transport='stop_position', uic_ref='8500007'),
        osm('72', 47.001, 8.0, public_transport='platform', uic_ref='8500007'),
        osm('73', 47.001, 8.00003, public_transport='stop_position', uic_ref='8500007'),
    ]
    output = match([stop, other], elements, SWITZERLAND)
    assert {unit.stop_kind for unit in output.osm_stop_units} == {'pair'}
    assert {(source, node, kind) for source, node, kind in links(output) if source == stop.source_id} == {
        (stop.source_id, '70', 'sloid_identifier'), (stop.source_id, '71', 'osm_pair_propagation'),
    }


def test_the_partner_also_follows_an_element_that_names_one_platform_listed_twice():
    first = atlas('ch:1:sloid:9:0:1', 47.0, 8.0, code='A', uic='8500009')
    second = atlas('ch:1:sloid:9:0:2', 47.0, 8.00001, code='A', uic='8500009')
    other = atlas('ch:1:sloid:9:0:3', 47.001, 8.0, code='B', uic='8500009')
    elements = [
        osm('90', 47.0, 8.00003, public_transport='platform', local_ref='A', uic_ref='8500009'),
        osm('91', 47.0, 8.0, public_transport='stop_position', uic_ref='8500009',
            ref__sloid=f'{first.source_id};{second.source_id}'),
        osm('92', 47.001, 8.00003, public_transport='platform', local_ref='B', uic_ref='8500009'),
        osm('93', 47.001, 8.0, public_transport='stop_position', uic_ref='8500009'),
    ]
    output = match(atlas_state([first, second, other]), elements, SWITZERLAND)
    assert {(source, node, kind) for source, node, kind in links(output) if node in ('90', '91')} == {
        (first.source_id, '91', 'sloid_identifier'), (second.source_id, '91', 'sloid_identifier'),
        (first.source_id, '90', 'osm_pair_propagation'), (second.source_id, '90', 'osm_pair_propagation'),
    }


def test_station_shortcuts_skip_a_station_whose_other_elements_an_identifier_linked():
    # Without the guard, the one untagged element left at the station would take
    # every remaining platform: by UIC reference, or as the only element with the name.
    tagged, near = (atlas(f'ch:1:sloid:10:0:{n}', 47.0, 8.0 + n * 0.0003, uic='8500010') for n in (1, 2))
    far = atlas('ch:1:sloid:10:0:3', 47.002, 8.0006, uic='8500010')
    elements = [
        osm('100', tagged.lat, tagged.lon, public_transport='platform', uic_ref='8500010', ref__sloid=tagged.source_id),
        osm('101', near.lat, near.lon, public_transport='platform', uic_ref='8500010', uic_name='Stop'),
    ]
    output = match([tagged, near, far], elements, SWITZERLAND)
    assert {(source, node) for source, node, _ in links(output)} == {(tagged.source_id, '100'), (near.source_id, '101')}
    assert all(not kind.endswith('_one_osm') for _, _, kind in links(output))
    assert [stop.source_id for stop in output.unmatched_source] == [far.source_id]


def test_a_tagged_member_of_an_atlas_duplicate_group_brings_the_group_along():
    first = atlas('ch:1:sloid:8:0:1', 47.0, 8.0, code='1', uic='8500008')
    second = atlas('ch:1:sloid:8:0:2', 47.0, 8.00001, code='1', uic='8500008')
    output = match(atlas_state([first, second]), [osm('80', 47.0, 8.0, ref__sloid=second.source_id)], SWITZERLAND)
    assert links(output) == {(second.source_id, '80', 'sloid_identifier'),
                             (first.source_id, '80', 'source_duplicate_propagation')}


def test_parser_and_query_keep_ways_that_carry_a_stop_identifier(tmp_path):
    from osm_public_transport_matcher.acquisition.overpass import build_public_transport_query
    query = build_public_transport_query()
    assert 'way(area.searchArea)["ref:sloid"];' in query and 'way(area.searchArea)["railway:ref:sloid"];' in query
    path = tmp_path / 'osm.xml'
    path.write_text("""<osm version="0.6">
      <node id="1" lat="47.0" lon="8.0"><tag k="public_transport" v="stop_position"/><tag k="uic_ref" v="8500001"/></node>
      <way id="2"><center lat="47.0" lon="8.0001"/><tag k="public_transport" v="platform"/><tag k="uic_ref" v="8500002"/></way>
      <way id="3"><center lat="47.0" lon="8.0002"/><tag k="public_transport" v="platform"/><tag k="uic_ref" v="8500001"/>
        <tag k="name" v="Platform way"/><tag k="ref:sloid" v="ch:1:sloid:1:0:1"/></way>
      <way id="4"><center lat="47.0" lon="8.0003"/><tag k="public_transport" v="platform"/></way>
    </osm>""", encoding='utf-8')
    nodes = {node.node_id: node.identifier_only for node in read_osm(path).get_all_nodes()}
    assert nodes == {'1': False, 'way_2': False, 'way_3': True}
