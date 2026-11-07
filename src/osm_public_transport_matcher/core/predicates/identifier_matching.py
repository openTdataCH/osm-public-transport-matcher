"""Identifier matching: OSM elements that name their exact source stop.

Swiss mappers tag platforms and stop positions with ``ref:sloid``, the ATLAS
identifier of one platform. Such a value decides on its own: no distance, name
or route evidence is needed, and every element that carries it is linked, for
example a platform and its stop position. The rule runs first, so the later
rules never give a tagged element to another stop.

A value names a stop only within the profile's ``long_distance``: a farther one
was usually copied from another stop. That element is left to the other rules
and its identifier is reported, like values that name no source stop or are
malformed.
"""
from osm_public_transport_matcher.core.identifiers import IdentifierIndex
from osm_public_transport_matcher.core.models import SourceEntity
from osm_public_transport_matcher.core.predicates import BasePredicate
from osm_public_transport_matcher.core.utils.common import haversine_distance

NOTES = 'The OSM element carries the identifier of the source stop'


class IdentifierPredicate(BasePredicate):
    """Link every OSM element to the source stops its identifier tags name."""

    def run(self, ctx):
        stops = ctx.source.get_all_rows_as_dict().values()
        for rule in ctx.profile.identifier_rules:
            index = IdentifierIndex(rule, stops)
            match_type = f'{rule.namespace}_identifier'
            # Stations are anchors and never matched.
            readings = [(node, reading) for node in ctx.osm.get_unmatched_nodes() if not node.is_station
                        for reading in (index.read(node, ctx.profile.long_distance),) if reading and reading.stops]
            named = {stop.key for _, reading in readings for stop, _, _ in reading.stops}
            for node, reading in readings:
                if ctx.osm.is_used(node.node_id):   # already taken as a partner
                    continue
                for stop, value, distance in reading.stops:
                    ctx.commit(_with_duplicates(ctx, stop, named), node, match_type, distance, NOTES,
                               evidence={'identifier': {'namespace': rule.namespace, 'value': value}})
                # The PTv2 partner of a tagged element follows it when the element names
                # one platform and the partner carries no identifier of its own.
                partner = ctx.osm.pair_partner(node.node_id)
                if (partner is not None and not ctx.osm.is_used(partner.node_id) and _one_platform(ctx, reading)
                        and not any(rule.is_well_formed(value) for value in rule.values(partner.tags))):
                    for stop, _, _ in reading.stops:
                        ctx.commit(stop, partner, 'osm_pair_propagation',
                                   haversine_distance(stop.lat, stop.lon, partner.lat, partner.lon),
                                   f'OSM group partner of: {node.node_id}',
                                   evidence={'representative_osm_id': node.node_id})


def _one_platform(ctx, reading):
    """Whether the named stops are one platform: one stop, or one source duplicate group."""
    keys = {stop.key for stop, _, _ in reading.stops}
    return len(keys) == 1 or keys <= set(ctx.source.duplicate_key_map.get(next(iter(keys)), ()))


def _with_duplicates(ctx, stop, named):
    """The stop, carrying the unmatched members of its source duplicate group.

    Committing it links them too (source duplicate propagation), as the other
    rules do; a member that an OSM element names is linked by its own value.
    """
    group = ctx.source.duplicate_key_map.get(stop.key, [])
    members = [ctx.source.get_by_key(key) for key in group
               if key != stop.key and key not in named and key not in ctx.source.matched_ids]
    return SourceEntity(stop, members, 'source_duplicate' if members else None)


def identified_stations(ctx):
    """Station references of the stops and elements that an identifier already linked.

    At such a station the free elements and stops are no longer the whole
    station, so the shortcuts of later rules that assume they are, one OSM
    element or one source stop for a station or a name, must not apply there.
    """
    stations = set()
    for record in ctx.all_matches:
        if 'identifier' in record.evidence:
            stations.update((record.source_node.station_ref, record.osm_node.station_ref))
    stations.discard(None)
    stations.discard('')
    return stations
