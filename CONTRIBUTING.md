# Contributing to OSM Public Transport Matcher

Install the engine in editable mode and run the supported checks before changing a rule:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[test,quality,gtfs,swiss,acquisition]'
make quality PYTHON=.venv/bin/python
```

Use `make quality-fast PYTHON=.venv/bin/python` while editing. The full command
matches CI and produces branch coverage and test reports. See
[quality evidence](quality/README.md) for the module catalog, dependency rules,
raw artifacts, and optional mutation sample.

For only the core, install `.[test]` and run `tests/test_api.py`, `tests/test_matching_regressions.py` and `tests/test_results.py`. Those tests use normalized records, do not import adapters and need no network, database or review application.

## Quality workflow for a change

Before editing, identify the responsibility in [the module catalog](quality/modules.yml):
source-neutral matching, external-data integrations, or result production and
engine tooling. Preserve its named invariants and update the source/test/docs
associations when adding or moving files. Ownership discovery includes new
nonignored files outside the usual directories; an unowned script is an error.

During development, run the affected regression tests and
`make quality-fast PYTHON=.venv/bin/python`. Before handing off an engine change,
run `make quality PYTHON=.venv/bin/python` and inspect any failure or skipped case in the JUnit
report, along with newly uncovered branches. The full command produces
`quality/raw/engine-tests.xml` and branch coverage JSON/XML. Record what ran and
its limitations; raw percentages do not replace negative and failure-path tests.

Use [the bounded mutation sample](quality/README.md#mutation-scope-provenance-and-surviving-mutants)
when changing coordinate validation, and select the full configured validator
when broader result-validation behavior changes. A surviving mutation calls for
inspection or a stronger behavioral test. The current sample is advisory, so a
zero process exit does not by itself mean all mutations were killed.

Generic policy types belong in `core/profile.py`; `osm_public_transport_matcher` and
`osm_public_transport_matcher.profiles` re-export them as the public API.
Keep acquisition, dataset profiles, integration composition, and result I/O out
of the core layer. Static import checks enforce those boundaries; review remains
necessary for dynamic imports and runtime effects.

For a result-format or producer/consumer change, update [the wire reference](documentation/Result%20bundle%20format.md),
the relevant fixtures, and both sides' compatibility tests. From the sibling app
checkout, run `make quality ENGINE_ROOT=../engine` to include independent consumer
validation and PostGIS publication checks. The engine's own command cannot
establish consumer compatibility or database rollback safety. Use the application
quality workflow to review combined structural debt and coverage trends.

Describe intended behavior, affected modules/contracts, preserved invariants,
acceptance and failure examples, the documentation decision, and any recovery
impact in the change review. Quality findings should be fixed or covered by a
reviewed, expiring exception where that mechanism applies; regenerating a debt
baseline is not an automatic part of a green build.

## Add or improve a predicate

1. Add a small failing example using `SourceStop` and `OsmNode` in `tests/`. Assert expected identity pairs, not only total counts. Include the ambiguous or negative case that could create a false positive.
2. Edit or add a class in `src/osm_public_transport_matcher/core/predicates/`. Subclass `BasePredicate` and implement `run(ctx)`. Read candidates from `ctx.source` and `ctx.osm` and record accepted links with `ctx.commit(...)`.
3. For a new matching rule, register its factory and its progress stage in `api._predicates` and add its identifier to the desired profile's `predicate_names`. Record its inputs, assumptions and ordering below or in the module docstring.
4. Run the affected regression and the core suite. Preserve group propagation and candidate locks. Build reusable attribute indexes once per rule. Batch queries can contain candidates consumed by an earlier iteration; filter those candidates before committing. The 100-reference regression prevents repeated full OSM scans during reference matching.

`ctx.commit` expands source duplicates and OSM pairs. The sides of a two-sided stop have their own predicate; never propagate a direct match to its middle. Station references intentionally support one-to-many and many-to-one links. Changing these allocation rules requires explicit regression cases.

Name a matching rule and its match types after the evidence they use, and give each branch that decides differently its own match type, so a match type tells a reviewer how a pair was found.

| Rule identifier | Inputs and assumptions | Default position |
|---|---|---|
| `identifier` | A profile `IdentifierRule`: OSM tags, such as `ref:sloid`, whose value equals one source stop's reference; within the long-distance cap. Elements kept only for their identifier are linked after all rules. | First, so that no weaker evidence gives a tagged element to another stop. Later rules skip their station-level shortcuts where it linked part of a station. |
| `two_sided_stop` | Profile-approved station references; two source stops, the two sides and the middle of a PTv2 two-sided stop. | Before reference matching, which would see the stop as one element. |
| `reference` | A profile rule with compatible reference namespace, scope and OSM tag. Platform codes refine station candidates. | Before weaker evidence. |
| `name` | Exact source name against OSM name/UIC-name/GTFS-name; platform code can disambiguate. | Before GTFS route/distance. |
| `gtfs_route` | Scoped GTFS route IDs and direction IDs, or direction labels; mutual unique best candidate. Where a candidate carries the stop's platform code, candidates labelled with another platform are left out. | Before distance. |
| `group_distance` | Name or approved station groups; maximum-cardinality assignment under the distance cap. | First distance rule. |
| `group_long_distance` | Same evidence under the profile's longer cap. | After `group_distance`. |
| `local_ref_distance` | Equal platform code and `local_ref` within the normal distance cap. | Before nearest-only rules. |
| `distance_only_candidate`, `distance_ratio`, `distance_only_candidate_second_pass` | Spatial candidates and compatible platform codes; only candidate or sufficient separation from the second candidate. | Last, in that order. |

Name equality alone can be ambiguous across cities. The generic profile caps name matching at 1 km; the Swiss profile does not cap it. Route labels are weaker evidence than scoped route IDs. Profile options make such tradeoffs visible.

## Support another dataset

Start with an external adapter that returns `adapters.base.AdapterResult`: a `SourceState` of `SourceStop` records, optionally with explicit duplicate groups and route evidence. It does not need registration inside `osm_public_transport_matcher`. Keep source IDs as strings and select a stable namespace without a colon. Preserve source-specific fields under namespaced `extensions`; core predicates must not require them.

Use existing GTFS parsing when possible. Add a profile with reference mappings, thresholds and any grouping policy. Never infer that equal stop IDs or route IDs from different feeds mean the same entity. If an OSM extract's unscoped GTFS tags are assigned to a feed, make that adapter configuration explicit.

Include an offline fixture covering at least one accepted match, one unmatched source stop, one unmatched OSM element and missing optional route data. Do not put file reading, pandas, environment lookups, Flask or SQLAlchemy in the core. Parser/acquisition dependencies belong in optional extras in `pyproject.toml`.

An adapter belongs in this distribution only when it supports a broadly reusable standard or a maintained first-party deployment. It must have stable namespaced identities, strict validation, deterministic explicit inputs, provenance/fingerprints, isolated optional dependencies, offline tests and an identified maintainer. One-off agency or national adapters should remain external. Network clients belong under `acquisition/`; format parsing belongs under `adapters/`; dataset-specific composition belongs under `integrations/`.

## Understand or report a bad match

Include the dataset namespace, source ID, OSM type/ID, observed rule, expected link and a small relevant extract. A regression should explain why an adjacent candidate is wrong. Do not include credentials, private dataset URLs or a national dump.

Result schemas are versioned separately from the engine package. Changing fields consumed by the review app requires updating `documentation/Result bundle format.md`, contract fixtures and importer compatibility checks. A failed engine run or bundle validation must not replace an earlier published bundle.

No commit or push is required to run tests or propose a patch. Preserve the repository's license notice when copying files.
