# Engine quality evidence

This directory defines responsibility ownership and the evidence collected for
`transport-matcher`. The engine can verify itself without the review application.
The application combines both repositories' reports and runs the independent
producer/consumer and PostGIS checks.

## Set up and run

Run these commands from the engine checkout. Use Python 3.13, matching CI. The
package itself supports Python 3.10 and newer; the quality toolchain is verified
on one explicit version. A Python virtual environment, Git, and `make` are
required. Installing dependencies needs package-registry access; the ordinary
engine tests use local fixtures and do not require network access, PostGIS, or
the review application.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[swiss,gtfs,acquisition,test,quality]'
make quality-fast PYTHON=.venv/bin/python
make quality PYTHON=.venv/bin/python
```

The optional extras and analyzer settings live in [pyproject.toml](../pyproject.toml).
`quality` installs pytest, pytest-cov, Ruff, Hypothesis, JSON Schema validation,
and Vulture. The full suite also needs the acquisition/GTFS/Swiss dependencies.
Installing the quality tools alone does not install all adapter dependencies.
The `test` extra supports focused core tests; `mutation` is an additional,
optional tool installation described below.

| Command | What it verifies or produces |
|---|---|
| `make quality-fast` | Catalog schema, ownership, documentation/test references, static import boundaries, and Ruff `E9`/`F` correctness rules across `src`, `tests`, and `quality` |
| `make quality-test` | The complete engine pytest suite, branch coverage JSON/XML, and JUnit XML |
| `make quality` | Both commands above; this is the ordinary engine CI gate |
| `make quality-mutation` | A selected sample of result-validator mutations and a report restricted to that sample |

Pass `PYTHON=.venv/bin/python` to every command when using the environment above.
The [Makefile](../Makefile) otherwise defaults to `python3.13`; it does not create
or select a virtual environment automatically. `quality-test` can be run alone
while iterating, but it does not run the fast checks.

`OUTPUT_DIR` defaults to `quality/raw`. Relative paths are relative to the engine
checkout. Use an absolute path when a caller in another checkout collects the
reports. For example, run this from the sibling application checkout:

```sh
make -C ../engine quality-test \
  PYTHON="$PWD/../engine/.venv/bin/python" \
  OUTPUT_DIR="$PWD/quality/raw"
```

`MUTATION_TARGET` chooses the optional mutation sample. Standard analyzer
variables such as `COVERAGE_FILE` and `RUFF_CACHE_DIR` can relocate scratch files;
the app runner does this when the engine checkout is mounted read-only.

## Ownership and catalog changes

[modules.yml](modules.yml) uses JSON syntax, which is also valid YAML, and is
validated against [modules.schema.json](modules.schema.json). Edit these manual
inputs when responsibilities or the public contract change:

| Module | Responsibility and risk |
|---|---|
| `engine.core-matching` | Critical: normalized matching, allocation, route comparison, problem detection, and related progress/statistics support |
| `engine.integrations` | High: acquisition, adapters, dataset profiles, composition, caches, and acquisition benchmarks |
| `engine.result-producer` | Critical: result validation/publication, public API and CLI, quality tooling, workflows, and deployment configuration |

Each module declares an owner, review date, responsibility, criticality,
invariants, entry points, source globs, test associations, documentation by
purpose, and contracts/dependencies. These are maintained judgments, not metrics
inferred from directory names. Tests may be associated with multiple modules.
The result bundle connects producer and consumer owners; it does not create a
second owner for the same source file.

[check.py](check.py) discovers tracked and nonignored untracked production files
through Git, independently of the ownership globs. Code/configuration suffixes
and explicitly named files such as `Makefile` must have exactly one owner.
`source_roots` records the intended scope; a new `accidental.py` or a script in
an undeclared directory is still discovered and fails when unowned. Source
archives without `.git` use a filesystem inventory with generated directories
pruned. The catalog's exclusions identify fixture data, test evidence, generated
metadata, bytecode, and historical/raw measurements.

When adding a file or moving a responsibility:

1. Select its responsibility owner and update that module's source globs if needed.
2. Associate the tests and update the relevant explanation, reference, operations,
   or decision documentation. Do not reduce criticality to accommodate weak tests.
3. Update entry points, invariants, contracts, and the review date when their
   meaning changes. Add exclusions only for a documented nonproduction category.
4. Run `make quality-fast`, then `make quality` for the complete engine evidence.

The standalone check verifies the schema, unique ownership/IDs, nonempty source
and test globs, existing documentation, and the import rules below. The app's
combined catalog check additionally resolves declared entry-point symbols and
cross-repository contracts. A schema-valid catalog does not prove an invariant;
its associated tests provide that evidence.

## Source boundaries and public policy compatibility

Source-neutral policy dataclasses live in
[core/profile.py](../src/transport_matcher/core/profile.py), alongside the
algorithms that consume them. This prevents generic algorithms from depending
on a package that also selects Swiss-specific policies.

`GroupingPolicy`, `MatchingProfile`, and `ReferenceRule` remain available through
`transport_matcher.profiles` as aliases of the same class objects. The existing
public `transport_matcher.MatchingProfile` and `ReferenceRule` imports also keep
working. `transport_matcher.profiles.SWITZERLAND` remains the dataset profile.
Importing the generic public API no longer imports that dataset-profile package
implicitly. See the compatibility assertion in
[test_architecture.py](../tests/test_architecture.py).

The engine's AST guard resolves absolute and relative static imports. Code under
`src/transport_matcher/core` cannot import acquisition, adapters, integrations,
dataset profiles, results, Swiss composition, or the CLI. Every package source
file is checked for imports of `backend` or `stop_sync_osm_atlas`. The combined
app check also rejects review-framework dependencies from the engine and engine
imports from the app. Parser/acquisition dependencies remain optional extras;
normalized matching does not acquire external data.

These checks cover explicit import syntax. They do not prove every dynamic
import or runtime dependency is safe. The public package facade necessarily
exports both matching and bundle functions; it is not itself the core layer.
Changes to public imports or dataset/core direction still need review.

## Tests, coverage, and artifacts

The property cases in [test_quality_properties.py](../tests/test_quality_properties.py)
exercise namespaced and typed identity round trips, conservation of matched and
unmatched entity sets, maximum-cardinality distance allocation against an
exhaustive oracle, and rejection of invalid wire coordinates. Explicit boundary
cases accept latitude ±90 and longitude ±180 and reject the immediately adjacent
out-of-range floating-point values. Property generation is deterministic and
bounded for these tests; it is not a proof over every possible input.

[Result tests](../tests/test_results.py) also cover corrupt bundles, incomplete
outputs, atomic publication failure, and concurrent writers. The
[architecture tests](../tests/test_architecture.py) cover import forms, public
policy aliases, and ownership discovery in Git checkouts and source archives.

| Generated path under `OUTPUT_DIR` | Evidence |
|---|---|
| `engine-python-coverage.json` | File-level statements, executed/missing lines, branches, and covered/missing branch counts |
| `engine-python-coverage.xml` | Cobertura-compatible coverage for external readers |
| `engine-tests.xml` | JUnit test outcomes, including failures and skips |
| `engine-mutation-provenance.json` | Mutation scope, start time, revision, and source/test SHA-256 digests captured before the run |
| `engine-mutation.json` | Selected mutation outcomes, counts, tool version, completion time, and provenance |
| `engine-mutation.txt` | Human-readable selected mutation outcomes |

Coverage is configured for all of `src/transport_matcher`, including source files
that tests never import. Workflow/configuration/tooling ownership does not add
those files to the runtime coverage denominator. The overall terminal coverage
percentage combines statements and branches; the app dashboard reports module
branch coverage separately. No arbitrary engine-wide percentage is enforced.
A passing assertion suite alone does not establish complete collection: the
app's full quality gate also checks branch collection, the production-file
inventory, valid counts, executed production statements, and current inputs.

Generated artifacts, `.coverage` data, caches, and `mutants/` are excluded from
Git; generated evidence/caches are also excluded from the engine Docker build
context. Keep raw evidence from failed runs for diagnosis. Do not hand-edit a
measurement or use a saved percentage as a claim about newer source.

## Mutation scope, provenance, and surviving mutants

Mutation testing deliberately changes implementation details and checks whether
tests detect the changes. It requires the optional mutmut dependencies and a
platform with process-fork support; the scheduled job runs on Linux.

```sh
.venv/bin/python -m pip install -e '.[mutation]'
make quality-mutation PYTHON=.venv/bin/python
```

The exact default target is
`transport_matcher.results.validation.*_position*`: the wire-coordinate
validator, within `engine.result-producer`. It is not a score for the full
validator, matching engine, or application. The underlying configuration only
mutates [results/validation.py](../src/transport_matcher/results/validation.py)
and selects [test_results.py](../tests/test_results.py) plus
[test_quality_properties.py](../tests/test_quality_properties.py). Mutmut may
generate candidates for the whole configured file before executing the selected
function sample, so initial generation can take longer than the selected tests.

To inspect the full configured validator instead:

```sh
make quality-mutation PYTHON=.venv/bin/python \
  MUTATION_TARGET='transport_matcher.results.validation.*'
```

[mutation_report.py](mutation_report.py) captures the engine revision, target
source digest, and both test-file digests before execution. It refuses to publish
a new report if the source, tests, or selected scope changed during that run, or
if the target matched no generated mutants. The JSON report lists only selected
mutants. Mutations outside the sample are not counted as successful or surviving.
This is target/test provenance; it is not a fingerprint of all transitive engine
inputs or a guarantee that two toolchain versions generate identical mutants.
Mutation provenance requires a Git checkout even though ordinary fast checks can
run from a source archive.

A `killed` mutation was detected by a test. A `survived` mutation needs inspection:
add a meaningful behavioral test if it exposes a gap, or document why the change
is equivalent. Timeouts, missing tests, and other incomplete outcomes are not
kills. To inspect a reported name without altering production source:

```sh
.venv/bin/python -m mutmut show transport_matcher.results.validation.x__position__mutmut_1
.venv/bin/python -m mutmut browse
```

Use the actual name from your report; generated mutation identifiers can change.
After strengthening a test, rerun the sample. The sample is currently advisory:
survivors do not make `make quality-mutation` fail merely because they survived.
Tool failures, an empty target, and changed provenance do fail it. Review the
artifact and scheduled job summary rather than interpreting a green process
exit as proof that every mutation was killed. Configuration details are in the
[mutmut reference](https://mutmut.readthedocs.io/en/latest/).

## Cross-repository verification and the application dashboard

From the sibling application checkout, `make quality ENGINE_ROOT=../engine`
provides the supported Python 3.13/Node 22/PostGIS environment. It uses separate
app and engine interpreters, runs both suites, and exercises the producer CLI
against the independently implemented consumer for GTFS and Swiss fixture
modes. App-only tests use checked-in result bytes without importing the engine.
Engine-only `make quality` does not run the app consumer or database tests.

The app's quality runner records both revisions, source fingerprints, tool
versions, and check results in `quality/raw/checks.json`. Its report generator
aggregates engine coverage and tests by this catalog. Copying standalone coverage
files alone does not establish that combined provenance; run the app's supported
full command to collect current cross-repository evidence.

The app full run clears its raw directory before collection. To include an
independently collected mutation sample, finish the full run first, then copy
`engine-mutation.json` into the app's `quality/raw` directory and regenerate the
app report with `python scripts/quality.py report --engine-root ../engine` in its
supported Python environment. The consumer checks the sample's source/test
hashes and requires a start time within the last seven days, allowing five
minutes of clock skew. Missing, changed, or old evidence is displayed as unmeasured,
invalid, or stale. A valid sample applies only to `engine.result-producer`.

For combined commands and publishing the generated portal artifacts, see the
[application quality guide](https://github.com/openTdataCH/stop_sync_osm_atlas/blob/main/quality/README.md).
Source revisions and hashes are evidence identifiers, not a deployment step.

## CI, baselines, and remaining limits

[tests.yml](../.github/workflows/tests.yml) runs on pushes and pull requests using
Python 3.13. It installs the full engine extras, runs `make quality`, uploads
`quality/raw/` even when checks fail, and verifies that the installed public
package works from outside the checkout. Its evidence artifact is named
`engine-quality-evidence`.

[quality-periodic.yml](../.github/workflows/quality-periodic.yml) runs the bounded
mutation sample on Mondays at 04:19 UTC and supports manual dispatch. It has a
20-minute timeout and uploads only the selected mutation JSON/text reports as
`engine-mutation-sample`. Its summary names engine maintainers as the review
owner. Workflow failure is visible in GitHub Actions; no bot issues or messages
are sent. Repository administrators still need to select required checks in
branch protection; a workflow file cannot configure that policy itself.

[security.yml](../.github/workflows/security.yml) runs on pull requests, Mondays
at 04:27 UTC, and manual dispatch. Python CodeQL analyzes `src` and publishes
findings to GitHub code scanning. The dependency job installs the engine with
all production extras (`acquisition`, `swiss`, and `gtfs`) in a fresh, separate
virtual environment. Pip's installation report supplies the exact resolved
third-party dependency closure; the local engine distribution is reviewed by
CodeQL and its tests, not looked up as a third-party PyPI release. Audit tooling
and test-only packages are outside this production closure.

Pinned `pip-audit` audits those exact dependency versions against the current
advisory service. Any reported vulnerability or failed dependency collection
fails the job; pip-audit does not provide a dependable high-severity-only gate.
Remediate or explicitly review findings rather than blanket-ignoring them. The
`engine-dependency-security-<sha>` artifact retains the installation report,
resolved requirements, and JSON audit result, including failures. Reproduce the
exact workflow steps locally or dispatch the workflow to refresh network-backed
advisory data. The dependency list is evidence from that environment, not a
deployment lockfile. Ordinary `make quality` remains independent of advisory
service availability, and no repository-wide scan is converted into a
per-module security percentage. Repository administrators must make the security
checks and desired CodeQL severity policy required in branch protection; CodeQL
workflow completion alone does not reject every finding.

Structural clone/dead-code baselines and expiring exceptions live in the
application's `quality/` directory and cover the combined ownership map. The
standalone engine command enforces Ruff's unused-code findings but does not run
that combined jscpd/Vulture ratchet. Baseline refreshes are deliberate, reviewed
changes after fixing findings; do not refresh merely to make a new clone pass.
Branch floors and changed-line coverage remain advisory during initial baseline
collection. There is no weighted quality score or broad mutation threshold.

A combined report can remain `partial` when required checks pass because some
lanes are intentionally not measured. Security job artifacts do not yet imply
per-module security measurements. Broad mutation coverage, type checking, full
browser E2E/visual checks, performance/query budgets, and historical agent
regression evaluations require separately reviewed work. Do not present those
as completed checks or invent historical tasks to populate an evaluation set.
