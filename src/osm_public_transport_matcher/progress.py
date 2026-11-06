"""Versioned, presentation-neutral progress events for pipeline consumers.

The catalog is the engine-owned source of truth for public acquisition,
matching, route and bundle substages.  Internal timing spans may be more
granular; only entries in this catalog are part of the progress contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import json
from typing import Iterable

from osm_public_transport_matcher.timing import stage


PROGRESS_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ProgressStage:
    id: str
    parent_id: str
    phase: str
    label: str
    description: str
    order: int
    progress_kind: str = "indeterminate"


ENGINE_PROGRESS_STAGES = (
    ProgressStage(
        "source_check.remote_sources", "source_check", "source_check",
        "Check ATLAS and timetable sources",
        "Probe the remote ATLAS and GTFS snapshots for freshness.", 10,
    ),
    ProgressStage(
        "atlas.prepare", "source_files", "source_files", "Download and prepare ATLAS stops",
        "Download or reuse ATLAS, then apply country, boundary, validity and platform filters.", 10,
    ),
    ProgressStage(
        "timetable.download", "source_files", "source_files", "Download timetable data",
        "Download and extract the GTFS source files, or reuse the verified source cache.", 20,
    ),
    ProgressStage(
        "timetable.patterns", "source_files", "source_files", "Prepare timetable patterns",
        "Filter stop times and reduce repeated trips into reusable route patterns.", 30,
    ),
    ProgressStage(
        "timetable.identities_routes", "source_files", "source_files",
        "Match GTFS stops to ATLAS and build timetable routes",
        "Resolve GTFS stops to ATLAS and build line families, itineraries and stop calls.", 40,
    ),
    ProgressStage(
        "osm.download", "source_files", "source_files", "Download OSM data from Overpass",
        "Request the Swiss public-transport snapshot and save the returned OSM XML.", 50,
    ),
    ProgressStage(
        "atlas.load", "matching_inputs", "matching_inputs", "Read ATLAS stops",
        "Read and normalize the selected local ATLAS snapshot.", 10,
    ),
    ProgressStage(
        "timetable.load", "matching_inputs", "matching_inputs", "Read cached timetable products",
        "Read local route, identity and source metadata products.", 20,
    ),
    ProgressStage(
        "timetable.local_feed", "matching_inputs", "matching_inputs", "Prepare the supplied GTFS feed",
        "Read the supplied GTFS feed and prepare stop identities and route products.", 30,
    ),
    ProgressStage(
        "osm.prepare", "matching_inputs", "matching_inputs", "Parse and prepare OSM data",
        "Parse stops, selected ways, route relations, memberships and route evidence.", 40,
    ),
    ProgressStage(
        "stop_matching.groups", "stop_matching", "stop_matching", "Build OSM stop groups",
        "Identify supported OSM pairs and PTv2 two-sided stops.", 10,
    ),
    ProgressStage(
        "stop_matching.two_sided_stop", "stop_matching", "stop_matching", "Two-sided stop matching",
        "Allocate two source platforms to the two sides of each PTv2 two-sided stop by total distance.", 20,
    ),
    ProgressStage(
        "stop_matching.reference", "stop_matching", "stop_matching", "Reference matching",
        "Match stops using profile-approved compatible identifiers.", 30,
    ),
    ProgressStage(
        "stop_matching.name", "stop_matching", "stop_matching", "Name matching",
        "Match remaining stops by exact name, using platform references to break ties.", 40,
    ),
    ProgressStage(
        "stop_matching.gtfs_route", "stop_matching", "stop_matching", "GTFS route matching",
        "Match remaining stops using shared GTFS route and direction evidence.", 50,
    ),
    ProgressStage(
        "stop_matching.distance", "stop_matching", "stop_matching", "Distance matching",
        "Run group, local_ref and nearest-distance allocation passes.", 60,
    ),
    ProgressStage(
        "stop_matching.unmatched", "stop_matching", "stop_matching",
        "Identify remaining unmatched stops",
        "Collect source stops and OSM elements left unmatched after every strategy.", 70,
    ),
    ProgressStage(
        "stop_matching.problems", "stop_matching", "stop_matching", "Detect data problems",
        "Detect distance, attribute, route-contradiction, unmatched and duplicate problems.", 80,
    ),
    ProgressStage(
        "route_matching.normalize", "route_matching", "route_matching", "Normalize route data",
        "Build comparable line-family, itinerary and ordered stop-call records.", 10,
    ),
    ProgressStage(
        "route_matching.families", "route_matching", "route_matching", "Match line families",
        "Pair source and OSM route families using exact and normalized identifiers.", 20,
    ),
    ProgressStage(
        "route_matching.itineraries", "route_matching", "route_matching", "Match itineraries",
        "Compare directions and ordered stop sequences inside matched route families.", 30,
    ),
    ProgressStage(
        "bundle.fingerprints", "bundle", "bundle", "Record source fingerprints",
        "Record the exact source files and metadata used by the run.", 10,
    ),
    ProgressStage(
        "bundle.metrics", "bundle", "bundle", "Calculate quality metrics",
        "Calculate aggregate distance and matching-quality diagnostics.", 20,
    ),
    ProgressStage(
        "bundle.serialize", "bundle", "bundle", "Serialize and validate the result bundle",
        "Write the versioned result snapshot, checksums and validation manifest.", 30,
    ),
)

_STAGES_BY_ID = {item.id: item for item in ENGINE_PROGRESS_STAGES}
_REPORTING_ENABLED = ContextVar("osm_public_transport_matcher_progress_reporting", default=False)


def validate_progress_catalog(stages: Iterable[ProgressStage] = ENGINE_PROGRESS_STAGES) -> None:
    items = tuple(stages)
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Progress stage IDs must be unique")
    for item in items:
        if not item.id or not item.parent_id or not item.phase or not item.label or not item.description:
            raise ValueError(f"Incomplete progress stage definition: {item!r}")
        if item.parent_id != item.phase:
            raise ValueError(f"Engine progress stages must be direct children of their phase: {item.id}")
        if item.progress_kind not in {"indeterminate", "determinate"}:
            raise ValueError(f"Unsupported progress kind for {item.id}: {item.progress_kind}")


def progress_plan(
    *,
    phases: set[str] | None = None,
    excluded_stage_ids: set[str] | None = None,
) -> list[dict]:
    excluded_stage_ids = excluded_stage_ids or set()
    return [
        asdict(item)
        for item in ENGINE_PROGRESS_STAGES
        if (phases is None or item.phase in phases) and item.id not in excluded_stage_ids
    ]


def emit_progress_plan(
    *,
    phases: set[str] | None = None,
    excluded_stage_ids: set[str] | None = None,
    stages: list[dict] | None = None,
) -> None:
    print(json.dumps({
        "event": "pipeline_plan",
        "progress_schema_version": PROGRESS_SCHEMA_VERSION,
        "stages": stages if stages is not None else progress_plan(phases=phases, excluded_stage_ids=excluded_stage_ids),
    }), flush=True)


def run_progress_plan(*, command: str, download=False, processed=False, gtfs=False) -> list[dict]:
    """Select public work using the same resolved inputs that execution uses."""
    phases = None if download else {"matching_inputs", "stop_matching", "route_matching", "bundle"}
    excluded = set()
    if not download:
        excluded.update({"atlas.prepare", "timetable.download", "timetable.patterns", "timetable.identities_routes", "osm.download"})
    if command != "swiss":
        excluded.add("atlas.load")
    if not processed:
        excluded.add("timetable.load")
    if command == "swiss" and not gtfs:
        excluded.add("timetable.local_feed")
    return progress_plan(phases=phases, excluded_stage_ids=excluded)


def validate_stage_order(stage_ids: Iterable[str | None]) -> None:
    """One public stage may group adjacent passes, but may not restart later."""
    if not _REPORTING_ENABLED.get():
        return
    seen = set()
    previous = None
    for stage_id in stage_ids:
        if stage_id is not None and stage_id != previous:
            if stage_id in seen:
                raise ValueError(f"Progress stage predicates must be adjacent: {stage_id}")
            seen.add(stage_id)
        previous = stage_id


@contextmanager
def progress_reporting():
    """Temporarily enable progress without adding output to library calls."""
    token = _REPORTING_ENABLED.set(True)
    try:
        yield
    finally:
        _REPORTING_ENABLED.reset(token)


def emit_stage_outcome(stage_id: str, outcome: str) -> None:
    spec = _STAGES_BY_ID[stage_id]
    if outcome not in {"reused", "skipped"}:
        raise ValueError(f"Unsupported progress outcome: {outcome}")
    if not _REPORTING_ENABLED.get():
        return
    print(json.dumps({
        "event": "stage_outcome",
        "progress_schema_version": PROGRESS_SCHEMA_VERSION,
        "stage_id": stage_id,
        "stage_spec": asdict(spec),
        "outcome": outcome,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }), flush=True)


def progress_stage(stage_id: str, *, timing_name: str | None = None, **details):
    """Return a timing context that also emits the public progress contract."""
    spec = _STAGES_BY_ID[stage_id]
    if not _REPORTING_ENABLED.get():
        return stage(timing_name, **details) if timing_name else nullcontext()
    return stage(
        timing_name or stage_id,
        progress_schema_version=PROGRESS_SCHEMA_VERSION,
        stage_id=spec.id,
        stage_spec=asdict(spec),
        **details,
    )


validate_progress_catalog()
