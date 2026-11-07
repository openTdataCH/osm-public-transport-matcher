"""Reference matching through profile-approved, scoped identifier mappings.

Each commit names the branch that decided it, prefixed with the rule namespace:
``<namespace>_reference_one_osm`` (one OSM element carries the value),
``<namespace>_reference_one_source`` (one source stop carries a station value) and
``<namespace>_reference_local_ref`` (platform code equals ``local_ref``).
"""
from collections import defaultdict

from osm_public_transport_matcher.core.predicates import BasePredicate
from osm_public_transport_matcher.core.predicates.identifier_matching import identified_stations
from osm_public_transport_matcher.core.utils.common import haversine_distance


class ReferencePredicate(BasePredicate):
    """Link stops through identifiers that a profile reference rule declares comparable."""

    def run(self, ctx):
        # Where an identifier already linked part of a station, its other elements
        # and stops are no longer the whole station: only platform codes may link
        # them here, not the station-level shortcuts.
        identified = identified_stations(ctx)
        for rule in ctx.profile.reference_rules:
            candidates_by_value = ctx.osm.get_all_unmatched_grouped(rule.osm_tag)
            groups = defaultdict(list)
            for stop in ctx.source.get_unmatched_records():
                for reference in stop.references:
                    if reference.namespace == rule.namespace and reference.scope == rule.scope and (
                        rule.source_namespace is None or stop.namespace == rule.source_namespace
                    ):
                        groups[(stop.namespace, reference.value)].append((stop, reference))
            for (_, value), entries in sorted(groups.items()):
                entries = [(stop, reference) for stop, reference in entries if stop.key not in ctx.source.matched_ids]
                if not entries:
                    continue
                candidates = candidates_by_value.get(value, [])
                candidates = [node for node in candidates if not ctx.osm.is_used(node.node_id)
                              and rule.accepts(entries[0][0], entries[0][1], node)]
                if not candidates:
                    continue
                if rule.scope != 'station' and len(entries) > 1:
                    ctx.diagnostics.append({
                        'code': 'ambiguous_reference', 'stage': self.name,
                        'source_keys': sorted(stop.key for stop, _ in entries),
                        'reference_namespace': rule.namespace, 'reference_value': value,
                        'reason': 'Multiple source stops declare the same stop-scoped identifier.',
                    })
                    continue
                # A station reference can link several platforms to one OSM station anchor.
                if rule.scope == 'station' and len(candidates) == 1 and value not in identified:
                    for stop, _ in entries:
                        self._commit(ctx, stop, candidates[0], rule, value, 'one_osm',
                                     'One OSM element carries the station reference')
                elif rule.scope == 'station' and len(entries) == 1 and value not in identified:
                    for osm in candidates:
                        self._commit(ctx, entries[0][0], osm, rule, value, 'one_source',
                                     'One source stop carries the station reference')
                else:
                    for stop, _ in entries:
                        available = [osm for osm in candidates if not ctx.osm.is_used(osm.node_id)]
                        if rule.scope != 'station' and len(available) == 1:
                            self._commit(ctx, stop, available[0], rule, value, 'one_osm',
                                         'One OSM element carries the reference')
                            continue
                        compatible = [osm for osm in available if stop.platform_code and (osm.local_ref or '').strip().lower() == stop.platform_code.strip().lower()]
                        if compatible and (rule.scope == 'station' or len(compatible) == 1):
                            self._commit(ctx, stop, compatible[0], rule, value, 'local_ref',
                                         'Reference and platform code equal to local_ref')

    @staticmethod
    def _commit(ctx, stop, osm, rule, value, branch, notes):
        before = len(ctx.all_matches)
        ctx.commit(source_node=stop, osm_node=osm, match_type=f'{rule.namespace}_reference_{branch}',
                   distance_m=haversine_distance(stop.lat, stop.lon, osm.lat, osm.lon), notes=notes)
        ctx.all_matches[before].evidence.update({
            'reference': {'namespace': rule.namespace, 'value': value, 'scope': rule.scope},
            'osm_tag': rule.osm_tag,
            'source_namespace': stop.namespace,
        })
