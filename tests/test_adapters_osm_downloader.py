from pathlib import Path
import csv

import pytest


class DummyResponse:
    def __init__(self, status_code=200, text="<osm></osm>", url="https://overpass-api.de/api/interpreter"):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.encoding = None


def test_overpass_query_and_route_parsing_live_in_separate_modules():
    from public_transport_matcher.acquisition.overpass import build_public_transport_query
    from public_transport_matcher.adapters.osm_routes import process_osm_routes_data

    assert 'ISO3166-1"="FR' in build_public_transport_query('fr')
    assert process_osm_routes_data('<osm/>', out_dir=None)['osm_route_relations'].empty


def test_query_overpass_posts_raw_query(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from public_transport_matcher.acquisition import overpass
    captured = {}

    class DummySession:
        def post(self, url, data=None, headers=None, timeout=None):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            captured["timeout"] = timeout
            return DummyResponse(text="<osm version='0.6'></osm>", url=url)

    result = overpass.query_overpass(session=DummySession())

    assert result == "<osm version='0.6'></osm>"
    assert captured["url"] == overpass.OVERPASS_URL
    assert isinstance(captured["data"], bytes)
    assert captured["data"].decode("utf-8").startswith("[out:xml]")
    assert '[type=route][route!=hiking]' in captured["data"].decode("utf-8")
    assert captured["headers"]["Content-Type"].startswith("text/plain")
    assert captured["timeout"] == (30, 600)
    assert Path("data/raw/osm_data.xml").read_text(encoding="utf-8") == result


def test_query_overpass_raises_on_http_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from public_transport_matcher.acquisition import overpass

    class DummySession:
        def post(self, url, data=None, headers=None, timeout=None):
            return DummyResponse(status_code=406, text="Not Acceptable", url=url)

    try:
        overpass.query_overpass(session=DummySession())
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected RuntimeError for Overpass HTTP failure")

    assert message == "Overpass request failed: https://overpass-api.de/api/interpreter: HTTP 406 (1 attempt)"


def test_query_overpass_retries_504_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from public_transport_matcher.acquisition import overpass
    monkeypatch.setattr(overpass, "OVERPASS_MAX_RETRIES", 2)
    monkeypatch.setattr(overpass, "OVERPASS_RETRY_BACKOFF_SECONDS", 0.25)
    sleeps = []
    monkeypatch.setattr(overpass.time, "sleep", sleeps.append)

    class DummySession:
        def __init__(self):
            self.calls = 0

        def post(self, url, data=None, headers=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                return DummyResponse(status_code=504, text="Gateway Timeout", url=url)
            return DummyResponse(text="<osm version='0.6'></osm>", url=url)

    session = DummySession()

    result = overpass.query_overpass(session=session)

    assert result == "<osm version='0.6'></osm>"
    assert session.calls == 2
    assert sleeps == [0.25]


def test_query_overpass_exhausts_retries_for_502(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from public_transport_matcher.acquisition import overpass
    monkeypatch.setattr(overpass, "OVERPASS_MAX_RETRIES", 2)
    monkeypatch.setattr(overpass, "OVERPASS_RETRY_BACKOFF_SECONDS", 0.5)
    sleeps = []
    monkeypatch.setattr(overpass.time, "sleep", sleeps.append)

    class DummySession:
        def __init__(self):
            self.calls = 0

        def post(self, url, data=None, headers=None, timeout=None):
            self.calls += 1
            return DummyResponse(status_code=502, text="Bad Gateway", url=url)

    session = DummySession()

    try:
        overpass.query_overpass(session=session)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected RuntimeError after retry exhaustion")

    assert session.calls == 3
    assert sleeps == [0.5, 1.0]
    assert message.endswith(": HTTP 502 (3 attempts)")


def test_query_overpass_retries_rate_limits_and_timeouts(monkeypatch, tmp_path):
    import requests
    monkeypatch.chdir(tmp_path)
    from public_transport_matcher.acquisition import overpass
    sleeps = []
    monkeypatch.setattr(overpass.time, "sleep", sleeps.append)
    answers = [DummyResponse(status_code=429, text="Too Many Requests"), requests.ReadTimeout("slow"),
               DummyResponse(text="<osm version='0.6'></osm>")]

    class DummySession:
        def post(self, url, **kwargs):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    result = overpass.query_overpass(session=DummySession(), max_retries=2, retry_backoff_seconds=1)

    assert result == "<osm version='0.6'></osm>"
    assert sleeps == [1, 2]


def test_query_overpass_rejects_runtime_errors_and_moves_to_the_next_endpoint(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from public_transport_matcher.acquisition import overpass
    sleeps = []
    monkeypatch.setattr(overpass.time, "sleep", sleeps.append)
    first, second, third = (f"https://{name}.example/api/interpreter" for name in ("first", "second", "third"))
    timed_out = ("<osm version='0.6'><node id='1' lat='47' lon='8'/>"
                 "<remark> runtime error: Query timed out in \"query\" at line 3 after 361 seconds. </remark></osm>")
    calls = []

    class DummySession:
        def post(self, url, **kwargs):
            calls.append(url)
            if url == first:
                return DummyResponse(text=timed_out, url=url)
            if url == second:
                return DummyResponse(status_code=403, text="<html><p>Forbidden</p></html>", url=url)
            return DummyResponse(text="<osm version='0.6'></osm>", url=url)

    with pytest.raises(RuntimeError) as failure:
        overpass.query_overpass(session=DummySession(), max_retries=1, retry_backoff_seconds=1,
                                overpass_url=f"{first}, {second}")

    assert calls == [first, first, second]
    assert sleeps == [1]
    assert str(failure.value) == (
        f"Overpass request failed: {first}: runtime error: Query timed out in \"query\" at line 3 "
        f"after 361 seconds. (2 attempts); {second}: HTTP 403 (1 attempt)"
    )
    assert not Path("data/raw/osm_data.xml").exists()

    calls.clear()
    assert overpass.query_overpass(session=DummySession(), overpass_url=f"{second},{third}") == "<osm version='0.6'></osm>"
    assert calls == [second, third]


@pytest.mark.parametrize("text, message", [
    ("<osm version='0.6'><node id='1'", "invalid XML"),
    ("", "invalid XML"),
    ("<html><body>Server busy</body></html>", "not an OSM document"),
])
def test_check_osm_snapshot_rejects_incomplete_answers(text, message):
    from public_transport_matcher.acquisition.overpass import OverpassError, check_osm_snapshot

    with pytest.raises(OverpassError, match=message):
        check_osm_snapshot(text)


def test_osm_base_time_reads_the_snapshot_header():
    from public_transport_matcher.acquisition.overpass import osm_base_time

    header = ('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n<note>ODbL</note>\n'
              '<meta osm_base="2026-10-31T19:03:06Z" areas="2026-10-31T14:54:51Z"/>\n</osm>')
    assert osm_base_time(header) == "2026-10-31T19:03:06Z"
    assert osm_base_time("<osm/>") is None


def test_process_osm_routes_data_writes_route_master_and_relation_csvs(monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from public_transport_matcher.adapters.osm_routes import process_osm_routes_data

        xml_data = """
        <osm version="0.6">
            <node id="100" lat="47.1" lon="8.1">
                <tag k="uic_ref" v="8500" />
                <tag k="name" v="Station A" />
                <tag k="public_transport" v="platform" />
            </node>
            <way id="200">
                <center lat="47.2" lon="8.2" />
                <tag k="uic_ref" v="8600" />
                <tag k="name" v="Station B" />
                <tag k="aerialway" v="station" />
                <tag k="public_transport" v="station" />
            </way>
            <relation id="300" version="1">
                <tag k="type" v="route" />
                <tag k="route" v="bus" />
                <tag k="name" v="Line 10 Outbound" />
                <tag k="ref" v="10" />
                <tag k="operator" v="Transit Co" />
                <tag k="gtfs:route_id" v="10" />
                <tag k="ref_trips" v="trip1.H" />
                <member type="node" ref="100" role="platform" />
                <member type="way" ref="200" role="platform" />
            </relation>
            <relation id="400" version="1">
                <tag k="type" v="route_master" />
                <tag k="route_master" v="bus" />
                <tag k="name" v="Line 10" />
                <tag k="ref" v="10" />
                <tag k="operator" v="Transit Co" />
                <tag k="gtfs:route_id" v="10" />
                <member type="relation" ref="300" role="" />
            </relation>
        </osm>
        """

        process_osm_routes_data(xml_data, out_dir="data/processed")

        with open("data/processed/osm_route_masters.csv", encoding="utf-8", newline="") as handle:
                route_master_rows = list(csv.DictReader(handle))
        with open("data/processed/osm_route_master_members.csv", encoding="utf-8", newline="") as handle:
                route_master_member_rows = list(csv.DictReader(handle))
        with open("data/processed/osm_route_relations.csv", encoding="utf-8", newline="") as handle:
                route_relation_rows = list(csv.DictReader(handle))
        with open("data/processed/osm_route_relation_stops.csv", encoding="utf-8", newline="") as handle:
                route_stop_rows = list(csv.DictReader(handle))
        assert route_master_rows[0]["route_master_id"] == "400"
        assert route_master_rows[0]["gtfs_route_id"] == "10"
        assert route_master_member_rows[0]["relation_id"] == "300"
        assert route_relation_rows[0]["relation_id"] == "300"
        assert route_relation_rows[0]["route_master_id"] == "400"
        assert route_relation_rows[0]["family_origin"] == "route_master"
        assert route_relation_rows[0]["synthetic_family_key"] == "route_master:400"
        assert route_stop_rows[0]["osm_node_id"] == "100"
        assert route_stop_rows[0]["uic_ref"] == "8500"
        assert route_stop_rows[1]["osm_node_id"] == "way_200"
        assert route_stop_rows[1]["stop_label"] == "Station B"
        assert route_stop_rows[1]["direction_id"] == "0"


def test_process_osm_routes_data_deduplicates_duplicate_route_master_relation_members(monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from public_transport_matcher.adapters.osm_routes import process_osm_routes_data

        xml_data = """
        <osm version="0.6">
            <relation id="300" version="1">
                <tag k="type" v="route" />
                <tag k="route" v="bus" />
                <tag k="ref" v="10" />
            </relation>
            <relation id="400" version="1">
                <tag k="type" v="route_master" />
                <tag k="route_master" v="bus" />
                <tag k="ref" v="10" />
                <member type="relation" ref="300" role="forward" />
                <member type="relation" ref="300" role="backward" />
            </relation>
        </osm>
        """

        process_osm_routes_data(xml_data, out_dir="data/processed")

        with open("data/processed/osm_route_master_members.csv", encoding="utf-8", newline="") as handle:
                route_master_member_rows = list(csv.DictReader(handle))

        assert route_master_member_rows == [{
                "run_id": route_master_member_rows[0]["run_id"],
                "route_master_id": "400",
                "relation_id": "300",
                "member_sequence": "0",
                "member_role": "forward",
        }]


def test_overpass_query_and_parser_share_the_lifecycle_definition():
    import re
    from public_transport_matcher.acquisition.overpass import build_public_transport_query
    from public_transport_matcher.core.osm_tags import LIFECYCLE_KEY_PATTERN, has_active_stop_tag, lifecycle_of

    query = build_public_transport_query()
    assert '["public_transport"~"platform|stop_position|station|halt|stop"]' in query
    assert f'[~"{LIFECYCLE_KEY_PATTERN}"~"' in query
    assert re.match(LIFECYCLE_KEY_PATTERN, 'disused:highway') and not re.match(LIFECYCLE_KEY_PATTERN, 'construction:highway')

    assert lifecycle_of({'disused:highway': 'bus_stop'}) == 'disused'
    assert lifecycle_of({'was:railway': 'halt', 'abandoned:railway': 'halt'}) == 'abandoned'
    assert lifecycle_of({'disused:shop': 'bakery', 'disused:highway': 'crossing'}) is None
    assert lifecycle_of({'construction:highway': 'bus_stop', 'highway': 'bus_stop'}) is None
    assert has_active_stop_tag({'disused:highway': 'bus_stop', 'public_transport': 'platform'})
    assert not has_active_stop_tag({'disused:highway': 'bus_stop', 'disused:public_transport': 'platform'})
