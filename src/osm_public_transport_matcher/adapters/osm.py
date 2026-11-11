"""Parse OSM extracts without requiring a database or application configuration."""
from __future__ import annotations
import logging
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from osm_public_transport_matcher.adapters.osm_routes import public_transport_relation_ids
from osm_public_transport_matcher.adapters.osm_stop_ways import stop_ways
from osm_public_transport_matcher.core.osm_tags import STOP_IDENTIFIER_KEYS
from osm_public_transport_matcher.core.state import OsmState, osm_node_from_entry

logger = logging.getLogger(__name__)


def read_osm(xml_file: str | Path, *, route_namespace: str | None = None, route_id_normalizer=None,
             identifier_keys: tuple[str, ...] = STOP_IDENTIFIER_KEYS) -> OsmState:
    """Read nodes and supported station ways, retaining ordered relation evidence.

    Nodes and ways keep distinct identities even when coordinates coincide.
    Which ways are stops, and where, is decided by ``stop_ways()``, the rule
    the route reader also follows. A way kept ``identifier_only`` (a platform
    with ``ref:sloid``) can be linked only by its identifier, and it adds
    nothing to the name, station-reference or direction evidence.
    """
    tree = ET.parse(xml_file)
    root = tree.getroot()

    all_nodes: dict[str, dict] = {}
    uic_ref_dict: dict[str, list] = defaultdict(list)
    name_index: dict[str, list] = defaultdict(list)

    # Collect element names/UICs for relation direction extraction
    element_id_to_name: dict[str, str] = {}
    element_id_to_uic: dict[str, str] = {}

    for node in root.iter("node"):
        node_id = node.get("id")
        try:
            lat = float(node.get("lat"))
            lon = float(node.get("lon"))
        except (ValueError, TypeError):
            continue

        local_ref = None
        tags: dict[str, str] = {}

        for tag in node.findall("tag"):
            k, v = tag.get("k"), tag.get("v")
            tags[k] = v
            if k == "local_ref":
                local_ref = v
            elif k == "ref" and not local_ref:
                local_ref = v

        entry = {
            'node_id': node_id,
            'lat': lat,
            'lon': lon,
            'local_ref': local_ref,
            'tags': tags,
        }
        all_nodes[str(entry['node_id'])] = entry

        if "uic_ref" in tags:
            uic_ref_dict[tags["uic_ref"]].append(entry)
            element_id_to_uic[node_id] = tags["uic_ref"]

        if "name" in tags:
            element_id_to_name[node_id] = tags["name"]

        for key in ('name', 'uic_name', 'gtfs:name'):
            if key in tags:
                name_index[tags[key]].append(entry)

    # Ways: the shared rule of osm_stop_ways decides which are stops.
    selected_way_count = 0
    for way in stop_ways(root, identifier_keys):
        tags = way.tags
        virtual_id = way.virtual_id
        way_uic_ref = tags.get("uic_ref")
        identifier_only = way.identifier_only
        entry = {
            'node_id': virtual_id,
            'lat': way.lat,
            'lon': way.lon,
            'local_ref': tags.get("local_ref") or tags.get("ref"),
            'tags': tags,
            'identifier_only': identifier_only,
        }

        all_nodes[str(entry['node_id'])] = entry
        selected_way_count += 1
        if identifier_only:
            continue

        if way_uic_ref:
            uic_ref_dict[way_uic_ref].append(entry)
            element_id_to_uic[virtual_id] = way_uic_ref

        if "name" in tags:
            element_id_to_name[virtual_id] = tags["name"]

        for key in ('name', 'uic_name', 'gtfs:name'):
            if key in tags:
                name_index[tags[key]].append(entry)

    # Extract per-node direction strings and route data from route relations (single pass)
    name_dirs: dict[str, set] = defaultdict(set)
    uic_dirs: dict[str, set] = defaultdict(set)
    node_routes: dict[str, list] = defaultdict(list)

    def _parse_direction_from_ref_trips(val: str):
        """H suffix → '0' (outbound), R suffix → '1' (inbound)."""
        if not val:
            return None
        for tid in val.split(','):
            tid = tid.strip()
            if tid.endswith('.H'):
                return '0'
            if tid.endswith('.R'):
                return '1'
        return None

    # Direction strings are derived from the relation pass directly.
    loaded_dirs_from_csv = False

    service_relation_ids = public_transport_relation_ids(root)
    for relation in root.iter("relation"):
        rel_tags: dict[str, str] = {t.get('k'): t.get('v') for t in relation.findall('./tag')}
        if rel_tags.get('type') != 'route' or relation.get('id') not in service_relation_ids:
            continue

        members: list[str] = []
        for member in relation.findall("./member"):
            member_ref = member.get('ref')
            if not member_ref:
                continue
            member_type = member.get('type')
            if member_type == 'node':
                members.append(member_ref)
            elif member_type == 'way':
                members.append(f"way_{member_ref}")
        if not members:
            continue

        # --- route data (always extracted) ---
        gtfs_route_id = rel_tags.get('gtfs:route_id')
        if gtfs_route_id and route_namespace is not None:
            gtfs_route_id = f'{route_namespace}:route:{gtfs_route_id}'
        route_name = rel_tags.get('name')
        direction_id = _parse_direction_from_ref_trips(rel_tags.get('ref_trips', ''))
        # If direction is unknown, create entries for both directions so the
        # matcher can still fall back to route_id-only matching.
        direction_ids = [direction_id] if direction_id is not None else ['0', '1']
        for did in direction_ids:
            route_entry = {
                'relation_id': relation.get('id'),
                'gtfs_route_id': gtfs_route_id,
                'route_id_normalized': route_id_normalizer(gtfs_route_id) if route_id_normalizer else gtfs_route_id,
                'direction_id': did,
                'route_name': route_name,
            }
            for nid in members:
                node_routes[nid].append(route_entry)

        # --- direction strings (only if not loaded from CSV) ---
        if not loaded_dirs_from_csv and len(members) >= 2:
            first, last = members[0], members[-1]
            fn = element_id_to_name.get(first)
            ln = element_id_to_name.get(last)
            if fn and ln:
                ds = f"{fn} → {ln}"
                for nid in members:
                    name_dirs[nid].add(ds)
            fu = element_id_to_uic.get(first)
            lu = element_id_to_uic.get(last)
            if fu and lu:
                ds = f"{fu} → {lu}"
                for nid in members:
                    uic_dirs[nid].add(ds)

    logger.info(
        f"Parsed OSM XML: {len(all_nodes)} stop elements "
        f"({selected_way_count} selected ways), "
        f"{len(uic_ref_dict)} uic_ref entries, "
        f"{len(name_dirs)} nodes with direction strings, "
        f"{len(node_routes)} nodes with route data"
    )
    return OsmState.from_records(
        [osm_node_from_entry(entry) for entry in all_nodes.values()],
        name_dirs={key: values for key, values in name_dirs.items() if key in all_nodes},
        uic_dirs={key: values for key, values in uic_dirs.items() if key in all_nodes},
        node_routes={key: values for key, values in node_routes.items() if key in all_nodes},
    )
