"""Overpass acquisition for public-transport stops and their route relations."""
from __future__ import annotations

import io
import os
from pathlib import Path
import re
import time
from typing import Any
import xml.etree.ElementTree as ET

from public_transport_matcher import __version__
from public_transport_matcher.core.osm_tags import (
    LIFECYCLE_KEY_PATTERN,
    LIFECYCLE_VALUE_PATTERN,
    STOP_TAG_SELECTORS,
)


# A comma-separated list is tried in order until one endpoint answers.
OVERPASS_URL = os.getenv("OVERPASS_API_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_USER_AGENT = os.getenv(
    "OVERPASS_USER_AGENT",
    f"public-transport-matcher/{__version__} (+https://github.com/openTdataCH/stop_sync_osm_atlas)",
)
OVERPASS_HEADERS = {
    "Content-Type": "text/plain; charset=utf-8",
    "Accept": "application/osm3s+xml, text/xml, application/xml;q=0.9, */*;q=0.1",
    "User-Agent": OVERPASS_USER_AGENT,
}
# Rate limits and busy or unreachable servers usually pass; other HTTP errors do not.
OVERPASS_RETRY_STATUS_CODES = {429, 502, 503, 504}
OVERPASS_MAX_RETRIES = int(os.getenv("OVERPASS_MAX_RETRIES", "2"))
OVERPASS_RETRY_BACKOFF_SECONDS = float(os.getenv("OVERPASS_RETRY_BACKOFF_SECONDS", "5"))


def build_public_transport_query(country_code: str = "CH") -> str:
    """Return the Overpass QL query for an ISO 3166-1 alpha-2 area."""
    if not country_code.isalpha() or len(country_code) != 2:
        raise ValueError("country_code must be a two-letter ISO code")
    stop_selectors = "\n".join(
        f'            node(area.searchArea)["{key}"{operator}"{value}"];'
        for key, operator, value in STOP_TAG_SELECTORS
    )
    return f'''
        [out:xml][timeout:360];
        area["ISO3166-1"="{country_code.upper()}"]->.searchArea;

        (
{stop_selectors}
            node(area.searchArea)[~"{LIFECYCLE_KEY_PATTERN}"~"{LIFECYCLE_VALUE_PATTERN}"];
        )->.pt_nodes;

        (
            way(area.searchArea)["aerialway"="station"]["public_transport"="station"];
            way(area.searchArea)["uic_ref"];
        )->.candidate_ways;

        (
            relation(bn.pt_nodes)[type=route][route!=hiking];
            relation(bw.candidate_ways)[type=route][route!=hiking];
        )->.seed_routes;

        relation(br.seed_routes)[type=route_master]->.route_masters;

        (
            .seed_routes;
            relation(r.route_masters)[type=route][route!=hiking];
        )->.routes;

        .pt_nodes out body qt;
        .candidate_ways out body center qt;
        .routes out meta;
        .route_masters out meta;
    '''.strip()


class OverpassError(RuntimeError):
    """One failed attempt; ``retryable`` says whether the same endpoint may succeed later."""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def overpass_endpoints(value: str | None = None) -> list[str]:
    endpoints = [url.strip() for url in (value or OVERPASS_URL).split(",") if url.strip()]
    if not endpoints:
        raise ValueError("OVERPASS_API_URL names no endpoint")
    return endpoints


def check_osm_snapshot(xml_text: str) -> None:
    """Raise unless the text is complete OSM XML without an Overpass runtime error.

    Overpass reports a query timeout or memory exhaustion inside an HTTP 200
    answer, as ``<remark> runtime error: …</remark>`` after any partial data.
    """
    element = None
    try:
        for _, element in ET.iterparse(io.StringIO(xml_text)):
            if element.tag == "remark" and "runtime error" in (element.text or ""):
                raise OverpassError(" ".join(element.text.split()), retryable=True)
            element.clear()
    except ET.ParseError as exc:
        raise OverpassError(f"invalid XML ({exc})", retryable=True) from exc
    if element is None or element.tag != "osm":
        raise OverpassError("the answer is not an OSM document", retryable=True)


def osm_base_time(xml_text: str) -> str | None:
    """Return Overpass's ``osm_base``: the time of the newest OSM edit the server had."""
    match = re.search(r'<meta\b[^>]*\bosm_base="([^"]+)"', xml_text[:4096])
    return match.group(1) if match else None


def _fetch_snapshot(client: Any, url: str, body: bytes, headers: dict[str, str]) -> str:
    import requests

    try:
        response = client.post(url, data=body, headers=headers, timeout=(30, 600))
    except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
        print(f"Overpass: no answer from {url}: {exc}", flush=True)
        raise OverpassError("timed out" if isinstance(exc, requests.Timeout) else "connection failed",
                            retryable=True) from exc
    if response.status_code != 200:
        # The error page's text goes to the log; the run reports the status only.
        page = " ".join(re.sub(r"<[^>]+>", " ", response.text or "").split())
        print(f"Overpass: HTTP {response.status_code} from {url}: {page[:300]}", flush=True)
        raise OverpassError(f"HTTP {response.status_code}",
                            retryable=response.status_code in OVERPASS_RETRY_STATUS_CODES)
    response.encoding = "utf-8"
    check_osm_snapshot(response.text)
    return response.text


def query_overpass(
    session: Any = None,
    *,
    output_path: str | Path | None = "data/raw/osm_data.xml",
    country_code: str = "CH",
    overpass_url: str | None = None,
    headers: dict[str, str] | None = None,
    max_retries: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> str:
    """Fetch one validated OSM XML snapshot; parsing is handled by adapter modules.

    Each endpoint is retried with a doubling wait while its failures are
    retryable; any other failure moves on to the next endpoint at once.
    """
    import requests

    client = session or requests
    request_body = build_public_transport_query(country_code).encode("utf-8")
    retries = OVERPASS_MAX_RETRIES if max_retries is None else max_retries
    backoff = OVERPASS_RETRY_BACKOFF_SECONDS if retry_backoff_seconds is None else retry_backoff_seconds
    failures = []
    for url in overpass_endpoints(overpass_url):
        for attempt in range(retries + 1):
            try:
                text = _fetch_snapshot(client, url, request_body, headers or OVERPASS_HEADERS)
            except OverpassError as exc:
                error = exc
                if not exc.retryable or attempt >= retries:
                    break
                time.sleep(backoff * (2 ** attempt))
            else:
                if output_path is not None:
                    path = Path(output_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                return text
        failures.append(f"{url}: {error} ({attempt + 1} attempt{'s' if attempt else ''})")
    raise RuntimeError("Overpass request failed: " + "; ".join(failures))
