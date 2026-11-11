"""Swiss ATLAS matching policy and its route-year normalization."""
import re
from osm_public_transport_matcher.core.osm_tags import STOP_IDENTIFIER_KEYS
from . import IdentifierRule, MatchingProfile, ReferenceRule

# A SLOID names a service point (ch:1:sloid:7000), an area of it
# (ch:1:sloid:7000:4) or a platform (ch:1:sloid:7000:4:7, ch:1:sloid:211::161536,
# ch:1:sloid:79089:0:A). ATLAS platforms always use the last form.
SLOID_PATTERN = r'ch:1:sloid:\d+(?::\d+|:\d*:[0-9A-Za-z]+)?'


def normalize_route_id(route_id):
    if route_id is None:
        return None
    text = str(route_id).strip()
    return re.sub(r'-j\d+', '-jXX', text) if text else None


SWITZERLAND = MatchingProfile(
    profile_id='switzerland',
    station_reference_matching=True,
    problem_name_tags=('uic_name',),
    preserve_name_alias_multiplicity=True,
    distance_priority_exempt_operators=('SBB',),
    osm_grouping=True,
    name_match_max_distance=None,
    reference_rules=(ReferenceRule('uic', 'uic_ref', scope='station', source_namespace='atlas'),),
    identifier_rules=(IdentifierRule('sloid', STOP_IDENTIFIER_KEYS, pattern=SLOID_PATTERN, source_namespace='atlas'),),
    capabilities=('stops', 'problems', 'routes'),
)
