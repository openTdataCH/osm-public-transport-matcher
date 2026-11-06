"""
Name-based matching predicate.

Matches source ``name`` against OSM ``name`` / ``uic_name`` /
``gtfs:name``. A single candidate commits ``name_one_osm``; among several, the
one whose ``local_ref`` equals the platform code commits ``name_local_ref``.
"""
from osm_public_transport_matcher.core.pipeline import MatchingContext
from osm_public_transport_matcher.core.predicates import BasePredicate
from osm_public_transport_matcher.core.utils.common import haversine_distance

class NamePredicate(BasePredicate):
    """Match source name against OSM name index."""

    def run(self, ctx: MatchingContext) -> None:
        for entry in ctx.source.get_unmatched_records():
            name = (entry.name or '').strip()
            if not name:
                continue

            candidates = ctx.osm.get_by_name(name)
            if not candidates:
                continue

            osm = None
            match_type = 'name_one_osm'
            if len(candidates) == 1:
                osm = candidates[0]
            else:
                match_type = 'name_local_ref'
                # Refine by platform_code == local_ref
                desig = (entry.platform_code or '').strip().lower()
                if desig:
                    for c in candidates:
                        if (c.local_ref or '').strip().lower() == desig:
                            osm = c
                            break

            if osm:
                dist = haversine_distance(
                    entry.lat, entry.lon,
                    osm.lat, osm.lon
                )
                if ctx.profile.name_match_max_distance is not None and dist > ctx.profile.name_match_max_distance:
                    continue
                ctx.commit(
                    source_node=entry,
                    osm_node=osm,
                    match_type=match_type,
                    distance_m=dist,
                    notes=f"Name index match ({len(candidates)} candidates)",
                    evidence={'source_name': name, 'candidate_count': len(candidates), 'platform_code': entry.platform_code},
                )
