import json
from pathlib import Path

import pytest

from transport_matcher.progress import (
    ENGINE_PROGRESS_STAGES,
    PROGRESS_SCHEMA_VERSION,
    emit_progress_plan,
    progress_reporting,
    progress_stage,
    validate_progress_catalog,
)


def _events(output):
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def test_progress_catalog_is_unique_ordered_and_directly_parented():
    validate_progress_catalog()
    ids = [stage.id for stage in ENGINE_PROGRESS_STAGES]
    assert len(ids) == len(set(ids))
    assert all(stage.parent_id == stage.phase for stage in ENGINE_PROGRESS_STAGES)
    assert [stage.id for stage in ENGINE_PROGRESS_STAGES if stage.parent_id == "source_files"] == [
        "atlas.prepare",
        "timetable.download",
        "timetable.patterns",
        "timetable.identities_routes",
        "osm.download",
    ]


def test_progress_plan_is_versioned_and_self_describing(capsys):
    emit_progress_plan()
    [event] = _events(capsys.readouterr().out)

    assert event["event"] == "pipeline_plan"
    assert event["progress_schema_version"] == PROGRESS_SCHEMA_VERSION
    assert any(stage["id"] == "stop_matching.unmatched" for stage in event["stages"])
    assert any(stage["label"] == "Download OSM data from Overpass" for stage in event["stages"])


def test_progress_plan_can_omit_inapplicable_work(capsys):
    emit_progress_plan(phases={"matching_inputs"}, excluded_stage_ids={"atlas.load", "timetable.load", "timetable.local_feed"})
    [event] = _events(capsys.readouterr().out)

    assert [stage["id"] for stage in event["stages"]] == ["osm.prepare"]


def test_progress_stage_emits_started_finished_and_failure(capsys):
    with progress_reporting():
        with progress_stage("stop_matching.exact"):
            pass
    success = _events(capsys.readouterr().out)

    assert [event["event"] for event in success] == ["stage_started", "stage_finished"]
    assert all(event["stage_id"] == "stop_matching.exact" for event in success)
    assert success[-1]["seconds"] >= 0

    with progress_reporting():
        with pytest.raises(RuntimeError, match="boom"):
            with progress_stage("stop_matching.name"):
                raise RuntimeError("boom")
    failed = _events(capsys.readouterr().out)

    assert [event["event"] for event in failed] == ["stage_started", "stage_failed"]
    assert failed[-1]["stage_spec"]["phase"] == "stop_matching"


def test_library_progress_is_silent_unless_explicitly_enabled(capsys):
    with progress_stage("stop_matching.exact"):
        pass
    assert capsys.readouterr().out == ""


def public_events(output):
    events = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and 'progress_schema_version' in value:
            events.append(value)
    return events


@pytest.mark.parametrize('mode', ['swiss', 'swiss-local-gtfs', 'gtfs'])
def test_real_cli_plan_matches_executed_work(mode, tmp_path, capsys):
    from transport_matcher.cli import main
    from transport_matcher.results import validate_bundle

    examples = Path(__file__).resolve().parents[1] / 'examples'
    if mode == 'gtfs':
        args = ['gtfs', '--source', str(examples / 'gtfs'), '--namespace', 'demo', '--osm', str(examples / 'osm.xml')]
    else:
        args = ['swiss', '--source', str(examples / 'swiss/stops_ATLAS.csv'),
                '--osm', str(examples / 'swiss/osm.xml'), '--processed', str(examples / 'swiss/processed')]
        if mode == 'swiss-local-gtfs':
            args += ['--gtfs', str(examples / 'gtfs')]
    destination = tmp_path / 'bundle'
    main([*args, '--output', str(destination)])
    events = public_events(capsys.readouterr().out)
    assert events[0]['event'] == 'pipeline_plan'
    planned = {spec['id'] for spec in events[0]['stages']}
    lifecycle = events[1:]
    started = [e['stage_id'] for e in lifecycle if e['event'] == 'stage_started']
    finished = [e['stage_id'] for e in lifecycle if e['event'] == 'stage_finished']
    assert set(started) == planned == set(finished)
    assert started == finished == [spec['id'] for spec in events[0]['stages']]
    assert 'osm.download' not in planned
    assert ('timetable.local_feed' in planned) == (mode != 'swiss')
    assert ('atlas.load' in planned) == (mode != 'gtfs')
    validate_bundle(destination)
    # Calling main in-process must not enable reporting for later library calls.
    with progress_stage('stop_matching.exact'):
        pass
    assert capsys.readouterr().out == ''


def test_missing_local_input_is_attributed_to_its_actual_stage(tmp_path, capsys):
    from transport_matcher.cli import main
    examples = Path(__file__).resolve().parents[1] / 'examples'
    with pytest.raises(FileNotFoundError):
        main(['swiss', '--source', str(tmp_path / 'missing.csv'), '--osm', str(examples / 'osm.xml'),
              '--output', str(tmp_path / 'bundle')])
    events = public_events(capsys.readouterr().out)
    assert events[-1]['event'] == 'stage_failed'
    assert events[-1]['stage_id'] == 'atlas.load'
    assert not any(e.get('stage_id') == 'osm.prepare' for e in events)


def test_reporting_rejects_nonadjacent_passes_before_matching():
    from types import SimpleNamespace
    from transport_matcher.core.pipeline import run_pipeline
    calls = []
    predicates = [SimpleNamespace(progress_stage_id=stage_id, run=lambda ctx: calls.append(ctx))
                  for stage_id in ('stop_matching.proximity', 'stop_matching.name', 'stop_matching.proximity')]
    with progress_reporting(), pytest.raises(ValueError, match='adjacent'):
        run_pipeline(predicates, None)
    assert not calls
