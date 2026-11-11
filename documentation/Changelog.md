# Matching engine changelog

This page records notable changes to the standalone `osm-public-transport-matcher` package, its adapters, matching behaviour, result production and command-line interface. Review-application releases are recorded separately in the [review application changelog](https://github.com/openTdataCH/stop_sync_osm_atlas/blob/main/documentation/Changelog.md).

Changes to the [result bundle contract](Result%20bundle%20format.md) are recorded in both changelogs: here from the producer side and in the application changelog from the consumer side.

---

## Version 0.1.0

First public release. The matching code of the review application becomes a package of its own, which the application now consumes through result bundles.

### Matching

- **Source-neutral core** — Stop matching, problem detection, route comparison and quality metrics run on normalized records, without file, network or database access. A `MatchingProfile` holds the rule order and thresholds; `profiles.SWITZERLAND` is the Swiss policy, and the generic default needs no SLOID or UIC identifiers.
- **Stop matching rules** — Identifier, reference, name, GTFS route and distance rules, in that order. Each match names the rule and branch that decided it, such as `sloid_identifier` or `gtfs_shared_route`. See [E2](2.%20Matching%20process.md#the-matching-rules).
- **Route comparison** — `compare_routes()` links source lines to OSM PTv2 route relations by route ID or by line number and shared stops, then pairs their itineraries stop by stop, counting another platform of the same station as the same stop. A relation without `ref_trips` has no direction and is paired on its stops in order; two different known directions are never paired. See [E3.2](3.2%20Route-route%20matching.md).
- **Problems** — Distance, unmatched, attribute, route contradiction, duplicate, disused and identifier problems, each with a priority from P1 to P3. See [E4.1](4.1%20Stop%20problems.md).

### Inputs

- **Adapters** — Generic GTFS (folder or ZIP), Swiss ATLAS with GTFS-to-SLOID resolution, and OSM XML. Other datasets implement the public `SourceAdapter` contract. See [E1](1.%20Input%20integrations%20%26%20acquisition.md).
- **Swiss acquisition** — Optional downloads of ATLAS, the Swiss GTFS feed, the Swiss boundary and an Overpass snapshot, with verified caches. Only public transport route relations are downloaded, and an invalid Overpass answer fails the run instead of being matched as a partial snapshot.

### Results and operations

- **Result bundle, schema 1** — A validated, checksummed and atomically published directory of stops, matches, unmatched entities, groups, problems, routes and diagnostics. See [Result bundle format](Result%20bundle%20format.md).
- **Command line** — `osm-public-transport-matcher swiss` and `osm-public-transport-matcher gtfs`, with structured progress events.
