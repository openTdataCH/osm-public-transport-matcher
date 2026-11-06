# Matching engine changelog

This page records notable changes to the standalone `osm-public-transport-matcher` package,
its adapters, matching behavior, result production and command-line interface.
Review-application releases are recorded separately in the
[review application changelog](https://github.com/openTdataCH/stop_sync_osm_atlas/blob/main/documentation/Changelog.md).

Changes to the [result bundle contract](Result%20bundle%20format.md) are recorded in both
changelogs: here from the producer side and in the application changelog from the
consumer side.

---

## Version 0.1.0

First public release.

### Matching

- **Source-neutral core** — Stop matching, problem detection, route comparison and quality metrics run on normalized records, without file, network or database access.
- **Dataset profiles** — `MatchingProfile` holds the rule order, reference rules, OSM grouping and thresholds; `profiles.SWITZERLAND` is the Swiss policy and the generic default needs no SLOID or UIC identifiers.
- **Match types name their evidence** — Each match records the rule and branch that decided it, such as `uic_reference_local_ref`, `gtfs_shared_route` or `local_ref_distance`; OSM stop units name their PTv2 layout, such as `ptv2_two_sided_stop`. See [E2](2.%20Matching%20process.md#the-matching-rules).
- **Route comparison** — Every adapter delivers `source_routes`, `source_itineraries` and `source_stop_calls`; `compare_routes()` matches line families and ordered itineraries against OSM PTv2 relations.
- **Disused OSM elements** — An element with a lifecycle-prefixed stop tag (`disused:`, `abandoned:`, `removed:`, `demolished:`, `razed:`, `destroyed:` or `was:`) is set aside before matching and returned in `MatchingOutput.disused_osm`. A source stop whose only candidate is disused is unmatched. See [E2](2.%20Matching%20process.md#rules-shared-by-all-matching-rules).
- **One OSM call per stop visit** — A PTv2 relation lists a stop's stop position and its platform one after the other. These two members form one stop call, which keeps the member resolved to a source stop, so an OSM itinerary has one call per stop like its source itinerary and the stop ratio compares like with like. Bundles carry one OSM `stop_calls` row per stop visit; `stop_sequence` follows the relation member positions. See [E3.2](3.2%20Route-Route%20Matching.md#step-3-build-itineraries-and-stop-calls).
- **`disused` problem** — P1 when an unmatched source stop shares the element's station reference, P2 when an unprefixed stop tag remains or a route relation still lists the element, none for a cleanly retired element. Disused elements get no `unmatched` problem. See [E4.1](4.1%20Stop%20Problems.md#disused-problems).

### Inputs

- **Adapters** — Generic GTFS (folder or ZIP), Swiss ATLAS with GTFS-to-SLOID resolution, and OSM XML. External datasets implement the public `SourceAdapter` contract without changes to this package.
- **Swiss acquisition** — Optional downloads with staged, verified caches; the Swiss GTFS feed is reduced to trip patterns in DuckDB.
- **Public transport routes only** — Route relations count when their `route` tag is a public transport mode (`bus`, `trolleybus`, `minibus`, `share_taxi`, `coach`, `tram`, `light_rail`, `train`, `subway`, `monorail`, `ferry`, `funicular`, `aerialway`), or when they have no `route` tag and belong to a route master of such a mode. Ski pistes, hiking, cycling and running routes and `route=railway` infrastructure relations pass through stops too, but are neither downloaded nor used as route evidence or route products. `PUBLIC_TRANSPORT_ROUTES` in `core/osm_tags.py` defines the list for the query and the parser. See [E1.3](1.3%20OSM%20data.md#which-route-relations-are-services).
- **Fully retired stops downloaded** — The Overpass query also selects elements whose every stop tag is lifecycle-prefixed, and seeds their route relations. `core/osm_tags.py` defines the stop selectors and lifecycle prefixes for both the query and the parser; the existing selector lines are unchanged. See [E1.3](1.3%20OSM%20data.md#disused-stops).
- **Validated Overpass snapshots** — An answer that does not parse as OSM XML, or that carries an Overpass `runtime error` remark inside HTTP 200, fails `osm.download` instead of being matched as a partial snapshot. HTTP 429 and 503, connection failures and timeouts are retried as well, `OVERPASS_API_URL` accepts a comma-separated list of endpoints, and the error names each endpoint's last failure without the error page. The snapshot's `osm_base` is recorded as `osm_data_at`. See [E1.3](1.3%20OSM%20data.md#the-download).
- **Named ATLAS and Swiss GTFS schema errors** — A missing required ATLAS column fails with `ATLAS CSV lacks required columns`; missing optional columns become warnings. The Swiss GTFS boundary selector rejects a feed without `original_stop_id`, `didok` or coordinates, and a selection with no stops. See [E1.1](1.1%20ATLAS%20stops.md#filters) and [E1.2](1.2%20GTFS%20ATLAS%20data.md).

### Results and operations

- **Result bundle, schema 1** — A validated, checksummed and atomically published directory of stops, matches, unmatched entities, groups, problems, routes, extensions and diagnostics, with source-neutral field names throughout.
- **Command line** — `osm-public-transport-matcher swiss` and `osm-public-transport-matcher gtfs`, with structured progress events (progress schema version 2).
- **Documentation** — Engine chapters on inputs, matching, routes, problems, operations and the bundle format, with generated diagrams.
- **Input warnings in the manifest, schema 1** — `metadata.warnings` lists input problems that weakened the run without stopping it. The field is optional and consumers ignore it until they show it. See [Result bundle format](Result%20bundle%20format.md#manifest).
- **Disused elements in the bundle, schema 1** — `osm_nodes` rows carry `lifecycle`, OSM `unmatched` rows a `disused` flag, disused elements are `single` stop units, and `metadata.summary.disused_osm` counts them. A missing `disused` counts as false, so earlier bundles stay valid. A consumer that predates the flag rejects a bundle with disused elements, because they have no `unmatched` problem; upgrade the consumer first. See [Result bundle format](Result%20bundle%20format.md#matches-and-unmatched-entities).
