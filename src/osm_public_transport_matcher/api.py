"""Public in-memory API: the same complete pipeline serves every data profile."""
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json

from .core.identifiers import IdentifierIndex
from .core.models import MatchingOutput, MatchRecord, OsmStopUnitRecord, OsmStopMemberRecord, DetectedProblem
from .core.state import SourceState, OsmState, PAIR_GROUP_KINDS
from .core.pipeline import MatchingContext, run_pipeline
from .core.route_state import RouteState
from .core.predicates.identifier_matching import NOTES as IDENTIFIER_NOTES, IdentifierPredicate
from .core.predicates.reference_matching import ReferencePredicate
from .core.predicates.name_matching import NamePredicate
from .core.predicates.two_sided_stop_matching import TwoSidedStopPredicate
from .core.predicates.distance_matching import GroupDistancePredicate, LocalRefDistancePredicate, NearestDistancePredicate
from .core.predicates.gtfs_route_matching import GtfsRoutePredicate
from .core.problem_detection.context import ProblemContext
from .core.problem_detection.pipeline import STOP_PROBLEM_PIPELINE
from .core.problem_detection.result import ProblemResult
from .core.problem_detection.stop_predicates.disused import conflicting_source_stops, disused_problem
from .core.problem_detection.stop_predicates.unmatched import unmatched_problem
from .core.utils.common import haversine_distance
from .core.profile import MatchingProfile
from .progress import progress_stage


def _predicates(profile):
    factories = {
        'identifier': IdentifierPredicate,
        'two_sided_stop': TwoSidedStopPredicate,
        'reference': ReferencePredicate,
        'name': NamePredicate,
        'gtfs_route': GtfsRoutePredicate,
        'group_distance': GroupDistancePredicate,
        'group_long_distance': lambda: GroupDistancePredicate(
            max_distance=profile.long_distance,
            match_type_suffix='group_long_distance',
            notes_label='Conflict-free max-cardinality long-distance group assignment',
            name='GroupLongDistancePredicate',
        ),
        'local_ref_distance': LocalRefDistancePredicate,
        'distance_only_candidate': lambda: NearestDistancePredicate(mode='single', pass_label='first'),
        'distance_ratio': lambda: NearestDistancePredicate(mode='ratio', pass_label='first'),
        'distance_only_candidate_second_pass': lambda: NearestDistancePredicate(mode='single', pass_label='second'),
    }
    unknown = set(profile.predicate_names) - factories.keys()
    if unknown:
        raise ValueError(f'Unknown predicates: {sorted(unknown)}')
    progress_stages = {
        'identifier': 'stop_matching.identifier',
        'two_sided_stop': 'stop_matching.two_sided_stop',
        'reference': 'stop_matching.reference',
        'name': 'stop_matching.name',
        'gtfs_route': 'stop_matching.gtfs_route',
        'group_distance': 'stop_matching.distance',
        'group_long_distance': 'stop_matching.distance',
        'local_ref_distance': 'stop_matching.distance',
        'distance_only_candidate': 'stop_matching.distance',
        'distance_ratio': 'stop_matching.distance',
        'distance_only_candidate_second_pass': 'stop_matching.distance',
    }
    predicates = []
    for name in profile.predicate_names:
        predicate = factories[name]()
        predicate.progress_stage_id = progress_stages[name]
        predicates.append(predicate)
    return predicates


def _build_groups(source, osm, profile):
    if not profile.osm_grouping:
        return
    nodes = [node for node in source.get_all_rows_as_dict().values() if profile.allows_station_reference(node)]
    by_reference = defaultdict(list)
    for node in nodes:
        by_reference[node.station_ref].append(node)
    safe_references = set()
    for reference, source_nodes in by_reference.items():
        osm_nodes = [osm._to_osm_node(entry) for entry in osm._uic_ref_dict.get(reference, [])]
        # A raw station value can occur in unrelated feeds/scopes. Do not
        # pre-group such buckets; scoped predicates can still resolve links.
        if len({node.namespace for node in source_nodes}) == 1 and all(
            profile.station_references_compatible(source_node, osm_node)
            for source_node in source_nodes for osm_node in osm_nodes
        ):
            safe_references.add(reference)
    nodes = [node for node in nodes if node.station_ref in safe_references]
    counts = Counter(node.station_ref for node in nodes if node.station_ref)
    names = {node.name: node.station_ref for node in nodes if node.name and node.station_ref}
    distances = defaultdict(list)
    for node in nodes:
        candidates = osm._uic_ref_dict.get(node.station_ref, [])
        if candidates:
            distances[node.station_ref].append(min(
                haversine_distance(node.lat, node.lon, osm_node['lat'], osm_node['lon'])
                for osm_node in candidates
            ))
    osm.build_groups(dict(counts), names, dict(distances), policy=profile.grouping)


def _stop_units(osm):
    units, grouped = [], set()
    for representative, (kind, siblings) in osm._group_siblings.items():
        if kind == 'ptv2_two_sided_stop':
            middle, first, second = osm.get_two_sided_stop_for_representative(representative)
            members = [OsmStopMemberRecord(middle, 'middle'), OsmStopMemberRecord(first, 'side'), OsmStopMemberRecord(second, 'side')]
            stop_kind = 'two_sided_stop'
        elif kind in PAIR_GROUP_KINDS and siblings:
            members = [OsmStopMemberRecord(representative, 'pair_a'), OsmStopMemberRecord(siblings[0].node_id, 'pair_b')]
            stop_kind = 'pair'
        else:
            continue
        units.append(OsmStopUnitRecord(stop_kind, kind, representative, members))
        grouped.update(member.node_id for member in members)
    for node in osm.get_all_nodes():
        if node.node_id not in grouped:
            units.append(OsmStopUnitRecord('single', None, node.node_id, [OsmStopMemberRecord(node.node_id, 'single')]))
    return units


def _problem(problem, source_keys=(), osm_ids=(), evidence=None):
    affected = [problem.problem_type, sorted(source_keys), sorted(osm_ids)]
    stable_id = sha256(json.dumps(affected, separators=(',', ':')).encode()).hexdigest()[:24]
    return DetectedProblem(
        stable_id, problem.problem_type, problem.priority, tuple(source_keys), tuple(osm_ids),
        {'has_source_duplicate': problem.has_source_duplicate,
         'has_osm_duplicate': problem.has_osm_duplicate, **(evidence or {})},
    )


def _link_identifier_only(nodes, source_stops, profile):
    """Link the elements kept only for their identifier tags, such as platform ways.

    They take part in no rule, so a link never takes a source stop away from
    another element. Returns the links and the elements left unlinked.
    """
    indexes = [IdentifierIndex(rule, source_stops) for rule in profile.identifier_rules]
    links, unlinked = [], []
    for node in nodes:
        found = []
        for index in indexes if not node.is_station else ():
            reading = index.read(node, profile.long_distance)
            match_type = f'{index.rule.namespace}_identifier'
            found.extend(MatchRecord(
                stop, node, match_type, distance, IDENTIFIER_NOTES,
                evidence={'rule': match_type, 'distance_m': distance,
                          'identifier': {'namespace': index.rule.namespace, 'value': value}},
            ) for stop, value, distance in (reading.stops if reading else ()))
        links.extend(found)
        if not found:
            unlinked.append(node)
    return links, unlinked


def _identifier_problems(profile, source_stops, nodes):
    """One problem per element whose identifier names a far stop, no stop, or is malformed."""
    for rule in profile.identifier_rules:
        index = IdentifierIndex(rule, source_stops)
        for node in nodes:
            reading = index.read(node, profile.long_distance)
            if reading is not None and reading.priority is not None:
                yield _problem(ProblemResult('identifier', reading.priority), osm_ids=[node.node_id],
                               evidence=reading.evidence(rule))


def _detect(output, profile, route_state, identifier_links=(), identifier_unlinked=()):
    """Detect problems, then add the elements kept only for their identifier.

    Those elements stay out of the problem context and the stop problems: their
    links come from their own tags. An unlinked one is kept only when its
    identifier is reported, and is then an unmatched element like any other.
    """
    ctx = ProblemContext.build(output)
    ctx.profile = profile
    ctx.route_state = route_state
    output.duplicate_osm_group_map = ctx.duplicate_osm_group_map
    matched_osm = {record.osm_node.node_id for record in output.matched}
    for unit in output.osm_stop_units:
        if unit.stop_kind == 'two_sided_stop':
            sides = [m.node_id for m in unit.members if m.member_role == 'side']
            if len(sides) == 2 and all(node_id in matched_osm for node_id in sides):
                output.effectively_matched_osm_ids.extend(m.node_id for m in unit.members if m.member_role == 'middle')
    problems = {}
    for record in output.matched:
        record.evaluate_matched_problems(ctx, STOP_PROBLEM_PIPELINE)
        for result in record.problems:
            p = _problem(result, [record.source_node.key], [record.osm_node.node_id], record.evidence)
            problems[p.problem_id] = p
    for node in output.unmatched_source:
        nearest = ctx.nearest_osm_distance(node.lat, node.lon)
        if nearest is None or nearest > profile.isolation_radius:
            output.source_isolated_keys.append(node.key)
        for predicate in STOP_PROBLEM_PIPELINE:
            for result in predicate(ctx, node):
                p = _problem(result, [node.key], evidence={'nearest_osm_distance_m': nearest})
                problems[p.problem_id] = p
    for node in output.unmatched_osm:
        nearest = ctx.nearest_source_distance(node.lat, node.lon)
        if nearest is None or nearest > profile.isolation_radius:
            output.isolated_osm_ids.append(node.node_id)
        if node.node_id in output.effectively_matched_osm_ids:
            continue
        for predicate in STOP_PROBLEM_PIPELINE:
            for result in predicate(ctx, node):
                p = _problem(result, osm_ids=[node.node_id], evidence={'nearest_source_distance_m': nearest})
                problems[p.problem_id] = p
    for node in output.disused_osm:
        for result in disused_problem(ctx, node):
            conflicts = sorted(stop.key for stop in conflicting_source_stops(ctx, node))
            p = _problem(result, osm_ids=[node.node_id], evidence={'lifecycle': node.lifecycle, 'conflicting_source_keys': conflicts})
            problems[p.problem_id] = p
    linked = list({record.osm_node.node_id: record.osm_node for record in identifier_links}.values())
    reported = set()
    for p in _identifier_problems(profile, output.all_source_nodes,
                                  [*output.all_osm_nodes, *linked, *identifier_unlinked]):
        problems[p.problem_id] = p
        reported.update(p.osm_ids)
    kept = [node for node in identifier_unlinked if node.node_id in reported]
    for node in kept:
        nearest = ctx.nearest_source_distance(node.lat, node.lon)
        if nearest is None or nearest > profile.isolation_radius:
            output.isolated_osm_ids.append(node.node_id)
        for result in unmatched_problem(ctx, node):
            p = _problem(result, osm_ids=[node.node_id], evidence={'nearest_source_distance_m': nearest})
            problems[p.problem_id] = p
    output.matched.extend(identifier_links)
    output.unmatched_osm.extend(kept)
    output.all_osm_nodes.extend([*linked, *kept])
    output.osm_stop_units.extend(
        OsmStopUnitRecord('single', None, node.node_id, [OsmStopMemberRecord(node.node_id, 'single')])
        for node in [*linked, *kept])
    output.problems = sorted(problems.values(), key=lambda p: (p.priority, p.problem_type, p.problem_id))


def match(source, osm, profile=None, route_evidence=None):
    """Match normalized records, returning matches, groups, unmatched entities and problems.

    Inputs are copied into fresh per-run states, so repeated calls and concurrent
    datasets do not inherit allocation or route state. Adapters are responsible for
    parser diagnostics and explicit namespace-scoped route identifiers.
    ``route_evidence`` may contain ``source``, ``osm``, and ``route_state`` overrides.
    """
    profile = profile or MatchingProfile()
    route_evidence = route_evidence or {}
    if isinstance(source, SourceState):
        source = SourceState(source.get_all_rows_as_dict().values(), source.duplicate_key_map,
                             route_evidence.get('source', source._route_evidence_by_key))
    else:
        source = SourceState(source, route_evidence_by_key=route_evidence.get('source'))
    if isinstance(osm, OsmState):
        nodes, node_routes, name_dirs, uic_dirs = (osm.get_all_nodes(), route_evidence.get('osm', osm._node_routes),
                                                   osm.name_dirs, osm.uic_dirs)
    else:
        nodes, node_routes, name_dirs, uic_dirs = list(osm), route_evidence.get('osm'), None, None
    # A lifecycle-prefixed stop tag (disused:highway=bus_stop) says the element
    # is out of service: it never takes part in matching and is reported apart.
    # Elements kept only for their identifier tags take part in no rule either.
    disused_osm = OsmState.from_records([node for node in nodes if node.lifecycle and not node.identifier_only],
                                        station_ref_tag=profile.osm_station_ref_tag).get_all_nodes()
    identifier_only = [node for node in nodes if node.identifier_only and not node.lifecycle]
    apart = {node.node_id for node in nodes if node.identifier_only}
    if apart:
        node_routes, name_dirs, uic_dirs = (
            {key: value for key, value in evidence.items() if key not in apart} if evidence else evidence
            for evidence in (node_routes, name_dirs, uic_dirs))
    osm = OsmState.from_records([node for node in nodes if not node.lifecycle and not node.identifier_only],
                                node_routes=node_routes, name_dirs=name_dirs, uic_dirs=uic_dirs,
                                station_ref_tag=profile.osm_station_ref_tag,
                                preserve_name_alias_multiplicity=profile.preserve_name_alias_multiplicity)
    route_state = route_evidence.get('route_state') or RouteState()
    with progress_stage('stop_matching.groups'):
        _build_groups(source, osm, profile)
    ctx = MatchingContext(source=source, osm=osm, max_distance=profile.max_distance,
                          profile=profile, route_state=route_state)
    result = run_pipeline(_predicates(profile), ctx)
    output = MatchingOutput(
        matched=result.matched, unmatched_source=result.unmatched_source,
        unmatched_osm=result.unmatched_osm, duplicate_key_map=source.duplicate_key_map,
        osm_stop_units=_stop_units(osm), disused_osm=disused_osm,
        all_source_nodes=list(source.get_all_rows_as_dict().values()),
        all_osm_nodes=osm.get_all_nodes(), source_route_evidence_by_key=source._route_evidence_by_key,
        osm_node_routes=osm._node_routes, osm_name_dirs=osm.name_dirs, profile_id=profile.profile_id,
        diagnostics=list(ctx.diagnostics),
    )
    has_source_routes = any(value.get('gtfs') for value in source._route_evidence_by_key.values())
    has_osm_routes = bool(osm._node_routes or osm.name_dirs)
    if not (has_source_routes and has_osm_routes):
        output.diagnostics.append({'code': 'route_evidence_unavailable', 'stage': 'route_matching',
                                   'reason': 'Source or OSM route evidence is absent; route-dependent checks skipped.'})
    identifier_links, identifier_unlinked = _link_identifier_only(identifier_only, output.all_source_nodes, profile)
    with progress_stage('stop_matching.problems'):
        _detect(output, profile, route_state, identifier_links, identifier_unlinked)
    output.metadata = {
        'profile': asdict(profile),
        'capabilities': [cap for cap in profile.capabilities if cap != 'routes' or (has_source_routes and has_osm_routes)],
        'summary': {'source_stops': len(output.all_source_nodes), 'osm_elements': len(output.all_osm_nodes),
                    'matches': len(output.matched), 'unmatched_source': len(output.unmatched_source),
                    'unmatched_osm': len(output.unmatched_osm), 'disused_osm': len(output.disused_osm),
                    'problems': len(output.problems)},
    }
    return output
