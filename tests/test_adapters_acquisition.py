from pathlib import Path
import shutil

import pytest

from transport_matcher.adapters import acquisition
from transport_matcher.adapters.source_freshness import source_snapshot_is_unchanged
from transport_matcher.progress import progress_reporting
from test_progress import public_events


@pytest.fixture
def sources(tmp_path, monkeypatch):
    calls = {'atlas': 0, 'gtfs': 0, 'osm': 0}
    versions = {'ATLAS': '1', 'GTFS': '1'}
    monkeypatch.setattr(acquisition, 'get_current_gtfs_permalink', lambda: 'https://example.com/gtfs.zip')
    monkeypatch.setattr(acquisition, 'probe_remote_source', lambda label, url: {
        'label': label, 'url': url, 'probe_ok': True, 'etag': versions[label]})
    monkeypatch.setattr(acquisition, '_ensure_swiss_geojson_cache', lambda path, **kw: Path(path).write_text('{}'))
    import transport_matcher.swiss
    monkeypatch.setattr(transport_matcher.swiss, '_boundary_selector', lambda path: lambda frame: frame.copy())

    def atlas(path, url, **kw):
        calls['atlas'] += 1
        Path(path).write_text('sloid;number;validTo;wgs84North;wgs84East;designation;designationOfficial\n'
                              'ch:1:sloid:A;8500001;9999-12-31;48;2;A;Alpha\n')
        return {'after_type_filter': 1}
    monkeypatch.setattr(acquisition, 'get_atlas_stops', atlas)

    def gtfs(url, path):
        calls['gtfs'] += 1
        shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/gtfs', path)
        return path
    monkeypatch.setattr(acquisition, 'download_and_extract_gtfs', gtfs)

    def osm(**kw):
        calls['osm'] += 1
        Path(kw['output_path']).write_text('<osm/>')
        return '<osm/>'
    monkeypatch.setattr(acquisition, 'query_overpass', osm)
    return calls, versions


def refresh(path, **kwargs):
    with progress_reporting():
        return acquisition.refresh_swiss(path, gtfs_url='https://example.com/gtfs.zip', **kwargs)


def test_refresh_reuses_verified_layers_and_always_refreshes_osm(tmp_path, sources, capsys):
    calls, versions = sources
    assert refresh(tmp_path)['preprocessing_reused'] is False
    cold = public_events(capsys.readouterr().out)
    assert not any(e['event'] == 'stage_outcome' for e in cold)
    assert refresh(tmp_path)['preprocessing_reused'] is True
    warm = public_events(capsys.readouterr().out)
    reused = {e['stage_id'] for e in warm if e['event'] == 'stage_outcome' and e['outcome'] == 'reused'}
    assert reused == {'atlas.prepare', 'timetable.download', 'timetable.patterns', 'timetable.identities_routes'}
    assert {e['stage_id'] for e in warm if e['event'] == 'stage_started'} == {'source_check.remote_sources', 'osm.download'}
    assert calls == {'atlas': 1, 'gtfs': 1, 'osm': 2}
    (tmp_path / 'data/processed/atlas_itineraries.csv').write_text('corrupted')
    third = refresh(tmp_path)
    assert third['cache_decisions']['gtfs_atlas'] == 'products_missing_or_changed'
    assert third['cache_decisions']['gtfs'] == 'hit'
    assert calls == {'atlas': 1, 'gtfs': 1, 'osm': 3}
    assert not list((tmp_path / 'data').glob('.acquisition-*'))


def test_atlas_change_reuses_gtfs_parsing_but_remaps_identities(tmp_path, sources):
    calls, versions = sources
    refresh(tmp_path)
    versions['ATLAS'] = '2'
    # Change the actual filtered ATLAS product as well as its remote validator.
    from unittest.mock import patch
    original = acquisition.get_atlas_stops
    def changed(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        Path(path).write_text(Path(path).read_text().replace('Alpha', 'Changed'))
        return result
    with patch.object(acquisition, 'get_atlas_stops', changed):
        metadata = refresh(tmp_path)
    assert metadata['cache_decisions']['gtfs'] == 'hit'
    assert metadata['cache_decisions']['gtfs_atlas'] == 'inputs_changed'
    assert calls == {'atlas': 2, 'gtfs': 1, 'osm': 2}


def test_successful_gtfs_checkpoint_survives_osm_failure(tmp_path, sources, monkeypatch, capsys):
    calls, _ = sources
    original = acquisition.query_overpass
    monkeypatch.setattr(acquisition, 'query_overpass', lambda **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError, match='offline'):
        refresh(tmp_path)
    events = public_events(capsys.readouterr().out)
    assert events[-1]['stage_id'] == 'osm.download'
    assert events[-1]['event'] == 'stage_failed'
    assert not any(e['event'] == 'stage_failed' for e in events[:-1])
    monkeypatch.setattr(acquisition, 'query_overpass', original)
    assert refresh(tmp_path)['preprocessing_reused'] is True
    assert calls == {'atlas': 1, 'gtfs': 1, 'osm': 1}


def test_gtfs_change_and_force_rebuild_required_layers(tmp_path, sources):
    calls, versions = sources
    refresh(tmp_path)
    versions['GTFS'] = '2'
    result = refresh(tmp_path)
    assert result['cache_decisions']['gtfs'] == 'source_changed_or_unverifiable'
    assert calls == {'atlas': 1, 'gtfs': 2, 'osm': 2}
    refresh(tmp_path, force=True)
    assert calls == {'atlas': 2, 'gtfs': 3, 'osm': 3}


def test_fixed_permalink_without_validator_does_not_establish_freshness():
    snapshot = {'probe_ok': True, 'final_url': 'https://example.com/current.zip', 'download_filename': 'current.zip'}
    assert source_snapshot_is_unchanged(snapshot, snapshot) is False


def test_download_times_survive_reuse_and_failed_processing(tmp_path, sources, monkeypatch):
    import json
    from datetime import datetime, timezone
    from types import SimpleNamespace

    _, versions = sources
    first = datetime(2026, 9, 27, 10, 20, 30, tzinfo=timezone.utc)
    later = datetime(2026, 9, 28, 11, 22, 33, tzinfo=timezone.utc)
    monkeypatch.setattr(acquisition, 'datetime', SimpleNamespace(now=lambda zone: first))
    original = acquisition.query_overpass
    monkeypatch.setattr(acquisition, 'query_overpass', lambda **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError, match='offline'):
        refresh(tmp_path)
    checkpoint = json.loads((tmp_path / 'data/source_metadata.json').read_text())
    for name in ('atlas', 'gtfs_source'):
        assert checkpoint['stages'][name]['source']['downloaded_at'] == first.isoformat()

    monkeypatch.setattr(acquisition, 'query_overpass', original)
    monkeypatch.setattr(acquisition, 'datetime', SimpleNamespace(now=lambda zone: later))
    warm = refresh(tmp_path)
    assert warm['last_overpass_query_at'] == later.isoformat()
    assert {s['downloaded_at'] for s in warm['sources'].values()} == {first.isoformat()}
    versions['GTFS'] = '2'
    changed = refresh(tmp_path)
    assert changed['sources']['atlas']['downloaded_at'] == first.isoformat()
    assert changed['sources']['gtfs']['downloaded_at'] == later.isoformat()


def test_old_cache_does_not_invent_download_times(tmp_path, sources):
    import json

    refresh(tmp_path)
    path = tmp_path / 'data/source_metadata.json'
    metadata = json.loads(path.read_text())
    for entry in [*metadata['sources'].values(), *(s['source'] for s in metadata['stages'].values() if s['source'])]:
        entry.pop('downloaded_at', None)
    path.write_text(json.dumps(metadata))
    reused = refresh(tmp_path)
    assert all(s['downloaded_at'] is None for s in reused['sources'].values())


def test_corrupt_parsed_cache_reuses_verified_download(tmp_path, sources, capsys):
    calls, _ = sources
    refresh(tmp_path)
    capsys.readouterr()
    (tmp_path / 'data/cache/gtfs/trip_patterns.parquet').write_bytes(b'corrupt')
    result = refresh(tmp_path)
    events = public_events(capsys.readouterr().out)
    assert any(e['stage_id'] == 'timetable.download' and e['event'] == 'stage_outcome' and e['outcome'] == 'reused' for e in events)
    assert any(e['stage_id'] == 'timetable.patterns' and e['event'] == 'stage_started' for e in events)
    assert result['cache_decisions']['gtfs'] == 'products_missing_or_changed'
    assert result['cache_decisions']['gtfs_source'] == 'hit'
    assert calls == {'atlas': 1, 'gtfs': 1, 'osm': 2}


@pytest.mark.parametrize('local_feed', [False, True])
def test_full_cli_plan_resolves_on_cold_and_warm_runs(tmp_path, sources, capsys, local_feed):
    from transport_matcher.cli import main
    feed = Path(__file__).resolve().parents[1] / 'examples/gtfs'
    for run in ('cold', 'warm', 'cached'):
        args = ['swiss', '--workspace', str(tmp_path), '--output', str(tmp_path / run)]
        if run != 'cached':
            args += ['--download']
        if local_feed:
            args += ['--gtfs', str(feed)]
        main(args)
        events = public_events(capsys.readouterr().out)
        plan = {spec['id'] for spec in events[0]['stages']}
        terminal = [e['stage_id'] for e in events if e['event'] in {'stage_finished', 'stage_outcome'}]
        assert plan == set(terminal)
        assert terminal == [spec['id'] for spec in events[0]['stages']]
        assert not any(e['event'] == 'stage_failed' for e in events)
        if not local_feed:
            import json
            manifest = json.loads((tmp_path / run / 'manifest.json').read_text())
            assert manifest['metadata']['acquisition'] == json.loads((tmp_path / 'data/source_metadata.json').read_text())


def test_expired_atlas_validity_invalidates_even_when_validator_is_unchanged(tmp_path, sources, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    today = datetime.now(timezone.utc)
    valid_to = (today + timedelta(days=1)).date().isoformat()
    original = acquisition.get_atlas_stops
    def dated(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        Path(path).write_text(Path(path).read_text().replace('9999-12-31', valid_to))
        return result
    monkeypatch.setattr(acquisition, 'get_atlas_stops', dated)
    refresh(tmp_path)
    monkeypatch.setattr(acquisition, 'datetime', SimpleNamespace(now=lambda zone: today + timedelta(days=2)))
    result = refresh(tmp_path)
    assert result['cache_decisions']['atlas'] == 'atlas_validity_expired'
    assert result['cache_decisions']['gtfs'] == 'hit'
