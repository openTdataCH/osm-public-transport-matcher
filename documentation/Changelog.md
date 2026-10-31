# Matching engine changelog

This page records notable changes to the standalone `public-transport-matcher` package,
its adapters, matching behavior, result production and command-line interface.
Review-application releases are recorded separately in the
[review application changelog](https://github.com/openTdataCH/stop_sync_osm_atlas/blob/main/documentation/Changelog.md).

Changes to the [result bundle contract](Result%20bundle%20format.md) are recorded in both
changelogs: here from the producer side and in the application changelog from the
consumer side.

---

## Unreleased

### Matching

- **Disused OSM elements** — An element with a lifecycle-prefixed stop tag (`disused:`, `abandoned:`, `removed:`, `demolished:`, `razed:`, `destroyed:` or `was:`) is set aside before matching and returned in `MatchingOutput.disused_osm`. Elements that kept an unprefixed stop tag were matched like active stops; they no longer are, so a source stop whose only candidate was one becomes unmatched. See [E2](2.%20Matching%20process.md#rules-shared-by-all-matching-rules).
- **`disused` problem** — P1 when an unmatched source stop shares the element's station reference, P2 when an unprefixed stop tag remains or a route relation still lists the element, none for a cleanly retired element. Disused elements get no `unmatched` problem. See [E4.1](4.1%20Stop%20Problems.md#disused-problems).

### Inputs

- **Fully retired stops downloaded** — The Overpass query also selects elements whose every stop tag is lifecycle-prefixed, and seeds their route relations. `core/osm_tags.py` defines the stop selectors and lifecycle prefixes for both the query and the parser; the existing selector lines are unchanged. See [E1.3](1.3%20OSM%20data.md#disused-stops).

### Results and operations

- **Disused elements in the bundle, schema 1** — `osm_nodes` rows carry `lifecycle`, OSM `unmatched` rows a `disused` flag, disused elements are `single` stop units, and `metadata.summary.disused_osm` counts them. A missing `disused` counts as false, so earlier bundles stay valid. A consumer that predates the flag rejects a bundle with disused elements, because they have no `unmatched` problem; upgrade the consumer first. See [Result bundle format](Result%20bundle%20format.md#matches-and-unmatched-entities).

## Version 0.2.0

First public release. Versions 0.1.x were internal pre-releases.

### Matching

- **Source-neutral core** — Stop matching, problem detection, route comparison and quality metrics run on normalized records, without file, network or database access.
- **Dataset profiles** — `MatchingProfile` holds the rule order, reference rules, OSM grouping and thresholds; `profiles.SWITZERLAND` is the Swiss policy and the generic default needs no SLOID or UIC identifiers.
- **Route comparison** — Every adapter delivers `source_routes`, `source_itineraries` and `source_stop_calls`; `compare_routes()` matches line families and ordered itineraries against OSM PTv2 relations.

### Inputs

- **Adapters** — Generic GTFS (folder or ZIP), Swiss ATLAS with GTFS-to-SLOID resolution, and OSM XML. External datasets implement the public `SourceAdapter` contract without changes to this package.
- **Swiss acquisition** — Optional downloads with staged, verified caches; the Swiss GTFS feed is reduced to trip patterns in DuckDB.

### Results and operations

- **Result bundle, schema 1** — A validated, checksummed and atomically published directory of stops, matches, unmatched entities, groups, problems, routes, extensions and diagnostics, with source-neutral field names throughout.
- **Command line** — `public-transport-matcher swiss` and `public-transport-matcher gtfs`, with structured progress events (progress schema version 2).
- **Documentation** — Engine chapters on inputs, matching, routes, problems, operations and the bundle format, with generated diagrams.
