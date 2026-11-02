"""Command-line acquisition and matching; the core API performs no file I/O."""

from __future__ import annotations

import argparse
import json
from osm_public_transport_matcher.progress import progress_reporting, emit_progress_plan, progress_stage, run_progress_plan
from pathlib import Path

from osm_public_transport_matcher.results import write_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description="Match public transport data against OSM and export a result bundle.")
    commands = parser.add_subparsers(dest="command", required=True)
    swiss = commands.add_parser("swiss", help="Run the Swiss ATLAS profile")
    swiss.add_argument("--source", type=Path, help="ATLAS stops CSV")
    swiss.add_argument("--osm", type=Path, help="OSM XML extract")
    swiss.add_argument("--processed", type=Path, help="Optional cached route/identity products")
    swiss.add_argument("--gtfs", type=Path, help="Optional GTFS folder to rebuild routes and identities")
    swiss.add_argument("--boundary", type=Path, help="Optional explicit GeoJSON boundary for GTFS filtering")
    swiss.add_argument("--workspace", type=Path, help="Acquisition workspace containing data/raw and data/processed")
    swiss.add_argument("--download", action="store_true", help="Refresh Swiss source snapshots in the workspace")
    swiss.add_argument("--force", action="store_true", help="Rebuild cached source products")
    swiss.add_argument("--output", required=True, type=Path, help="New result directory (must not exist)")
    gtfs = commands.add_parser("gtfs", help="Run a generic GTFS feed without Swiss identifiers")
    gtfs.add_argument("--source", required=True, type=Path, help="GTFS directory or ZIP")
    gtfs.add_argument("--namespace", required=True, help="Stable agency/feed namespace")
    gtfs.add_argument("--osm", required=True, type=Path)
    gtfs.add_argument("--output", required=True, type=Path)
    for command in (swiss, gtfs):
        command.add_argument('--compression-level', type=int, choices=range(10), default=1,
                             help='Bundle gzip level, 0–9 (default: 1 for faster publication)')
    args = parser.parse_args(argv)
    if args.command == "swiss":
        workspace = args.workspace.resolve() if args.workspace else None
        if args.download and workspace is None:
            parser.error("--download requires --workspace")
        if workspace:
            args.source = args.source or workspace / "data/raw/stops_ATLAS.csv"
            args.osm = args.osm or workspace / "data/raw/osm_data.xml"
            args.processed = args.processed or workspace / "data/processed"
            boundary = workspace / "data/raw/switzerland.geojson"
            args.boundary = args.boundary or (boundary if boundary.exists() else None)
        if not args.source or not args.osm:
            parser.error("swiss requires --source and --osm, or --workspace")
    with progress_reporting():
        emit_progress_plan(stages=run_progress_plan(
            command=args.command, download=getattr(args, "download", False),
            processed=getattr(args, "processed", None) is not None,
            gtfs=getattr(args, "gtfs", None) is not None,
        ))
        _execute(args)


def _execute(args):
    metadata = {}
    if args.command == "swiss":
        from osm_public_transport_matcher.integrations.swiss import run_matching
        workspace = args.workspace.resolve() if args.workspace else None
        if args.download:
            from osm_public_transport_matcher.integrations.swiss import refresh_swiss
            metadata["acquisition"] = refresh_swiss(workspace, force=args.force)
            args.boundary = args.boundary or workspace / "data/raw/switzerland.geojson"
        elif workspace and not args.gtfs and all((
            args.source.resolve() == workspace / 'data/raw/stops_ATLAS.csv',
            args.osm.resolve() == workspace / 'data/raw/osm_data.xml',
            args.processed.resolve() == workspace / 'data/processed',
        )):
            # Matching cached workspace inputs must carry their original provenance.
            source_metadata = workspace / 'data/source_metadata.json'
            if source_metadata.exists():
                metadata['acquisition'] = json.loads(source_metadata.read_text(encoding='utf-8'))
        result = run_matching(args.source, args.osm, args.processed, args.gtfs, boundary_geojson=args.boundary)
    else:
        from osm_public_transport_matcher.adapters.gtfs import run_gtfs
        result = run_gtfs(args.source, args.osm, namespace=args.namespace)
    from osm_public_transport_matcher.statistics import compute_quality_metrics
    with progress_stage("bundle.metrics", timing_name="bundle.quality_metrics"):
        result.extensions["quality_metrics"] = compute_quality_metrics(result.matched, result.all_osm_nodes, result.osm_stop_units)
    with progress_stage("bundle.serialize", timing_name="bundle.export_validate"):
        path = write_bundle(result, args.output, metadata=metadata, compresslevel=args.compression_level)
    print(json.dumps({"event": "result_published", "path": str(path)}))


if __name__ == "__main__":
    main()
