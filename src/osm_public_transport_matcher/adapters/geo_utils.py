"""The Swiss boundary: download it, read it and select the points inside it."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

SWISS_BOUNDARY_URL = 'https://raw.githubusercontent.com/ZHB/switzerland-geojson/master/country/switzerland.geojson'


def download_boundary(path: str | Path, url: str = SWISS_BOUNDARY_URL) -> None:
    """Download the boundary to ``path``, replacing the previous copy only with one that reads as a boundary."""
    import requests

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.part')
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        partial.write_bytes(response.content)
        load_boundary(partial)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f'Failed to download the Swiss boundary from {url}: {exc}') from exc
    os.replace(partial, path)


def load_boundary(path: str | Path):
    """Read a GeoJSON polygon; the geometries of a FeatureCollection are joined into one."""
    from shapely.geometry import MultiPolygon, Polygon, shape
    from shapely.ops import unary_union

    data = json.loads(Path(path).read_text(encoding='utf-8'))
    kind = data.get('type') if isinstance(data, dict) else None
    if kind == 'FeatureCollection':
        geometries = [shape(feature['geometry']) for feature in data.get('features', []) if feature.get('geometry')]
        if not geometries:
            raise ValueError('The boundary FeatureCollection has no geometries')
        boundary = geometries[0] if len(geometries) == 1 else unary_union(geometries)
    elif kind == 'Feature':
        boundary = shape(data['geometry'])
    else:
        boundary = shape(data)
    if not isinstance(boundary, (Polygon, MultiPolygon)):
        raise ValueError(f'The boundary is a {boundary.geom_type}, not a polygon')
    return boundary


def inside_boundary(boundary, lat, lon) -> np.ndarray:
    """For each point, whether it lies inside the boundary or on its edge."""
    import shapely

    shapely.prepare(boundary)
    return shapely.covers(boundary, shapely.points(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)))
