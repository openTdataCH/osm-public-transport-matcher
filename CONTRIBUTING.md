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

For only the core, install `.[test]` and run `tests/test_api.py`, `tests/test_matching_regressions.py` and `tests/test_results.py`. Those tests use normalised records, do not import adapters and need no network, database or review application.

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
when broader result-validation behaviour changes. A surviving mutation calls for
inspection or a stronger behavioural test. The sample is advisory, so a
zero process exit does not by itself mean all mutations were killed.

Generic policy types belong in `core/profile.py`; `osm_public_transport_matcher` and
`osm_public_transport_matcher.profiles` re-export them as the public API.
Keep acquisition, dataset profiles, integration composition, and result I/O out
of the core layer. Static import checks enforce those boundaries; review remains
necessary for dynamic imports and runtime effects.

For a result-format or producer/consumer change, update [the wire reference](documentation/Result%20bundle%20format.md),
the relevant fixtures, and both sides' compatibility tests. From the sibling app
checkout, run `make quality` to include independent consumer
validation and PostGIS publication checks. The engine's own command cannot
establish consumer compatibility or database rollback safety. Use the application
quality workflow to review combined structural debt and coverage trends.

Describe intended behaviour, affected modules/contracts, preserved invariants,
acceptance and failure examples, the documentation decision, and any recovery
impact in the change review. New duplicated blocks fail the combined clone gate
unless refactored or deliberately added to the reviewed baseline in the
application checkout; regenerating a debt baseline is not an automatic part of a
green build.

## Add or improve a predicate

1. Add a small failing example using `SourceStop` and `OsmNode` in `tests/`. Assert expected identity pairs, not only total counts. Include the ambiguous or negative case that could create a false positive.
2. Edit or add a class in `src/osm_public_transport_matcher/core/predicates/`. Subclass `BasePredicate` and implement `run(ctx)`. Read candidates from `ctx.source` and `ctx.osm` and record accepted links with `ctx.commit(...)`.
3. For a new matching rule, register its factory and its progress stage in `api._predicates` and add its identifier to the desired profile's `predicate_names`. Use an existing stage ID from `ENGINE_PROGRESS_STAGES` in `progress.py`, or add one as [E5.3](documentation/5.3%20Pipeline%20progress%20contract.md#adding-or-changing-a-stage) describes; rules that share a stage must be adjacent in the order. [E2 The matching rules](documentation/2.%20Matching%20process.md#the-matching-rules) lists every rule with its evidence and match types and explains why the rules run in that order; add the new rule there.
4. Run the affected regression and the core suite. Preserve group propagation and candidate locks. Build reusable attribute indexes once per rule. Batch queries can contain candidates consumed by an earlier iteration; filter those candidates before committing. The 100-reference regression prevents repeated full OSM scans during reference matching.

`ctx.commit` expands source duplicates and OSM pairs. The sides of a two-sided stop have their own predicate; never propagate a direct match to its middle. Station references intentionally support one-to-many and many-to-one links. Changing these allocation rules requires explicit regression cases.

Name a matching rule and its match types after the evidence they use, and give each branch that decides differently its own match type, so a match type tells a reviewer how a pair was found.

## Support another dataset

Start with an external adapter that returns `adapters.base.AdapterResult`: a `SourceState` of `SourceStop` records, optionally with explicit duplicate groups and route evidence. It does not need registration inside `osm_public_transport_matcher`. Preserve source-specific fields under namespaced `extensions`; core predicates must not require them.

Use existing GTFS parsing when possible. Add a [dataset profile](documentation/2.%20Matching%20process.md#dataset-profiles) with reference mappings, thresholds and any grouping policy. Never infer that equal stop IDs or route IDs from different feeds mean the same entity. If an OSM extract's unscoped GTFS tags are assigned to a feed, make that adapter configuration explicit.

[What belongs in this distribution](documentation/1.%20Input%20integrations%20%26%20acquisition.md#what-belongs-in-this-distribution) lists the identity, core and test requirements an adapter must meet and decides whether it belongs here or stays external; [Code structure](documentation/1.%20Input%20integrations%20%26%20acquisition.md#code-structure) shows where parsing, acquisition and composition live.

## Understand or report a bad match

Include the dataset namespace, source ID, OSM type/ID, observed rule, expected link and a small relevant extract. A regression should explain why an adjacent candidate is wrong. Do not include credentials, private dataset URLs or a national dump.

Result schemas are versioned separately from the engine package. Changing fields consumed by the review app requires updating `documentation/Result bundle format.md`, contract fixtures and importer compatibility checks. A failed engine run or bundle validation must not replace an earlier published bundle.

No commit or push is required to run tests or propose a patch. Preserve the repository's licence notice when copying files.
