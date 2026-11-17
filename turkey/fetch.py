"""Download raw layers into data/raw and record what was fetched in manifest.json."""

import hashlib
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

UA = {"User-Agent": "turkeytalk-gis-analysis/0.1"}


def _get(url: str, timeout: int = 180, tries: int = 3) -> bytes:
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception:
            if attempt == tries:
                raise
            time.sleep(3 * attempt)


def _record(manifest: dict, name: str, path: Path, url: str) -> None:
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    prev = manifest.get(name, {})
    # Keep the original download time when the cached file is unchanged.
    fetched = prev.get("fetched_utc") if prev.get("sha256") == sha else None
    manifest[name] = {
        "file": path.name,
        "url": url,
        "sha256": sha,
        "bytes": path.stat().st_size,
        "fetched_utc": fetched or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_cambridge(cfg: dict, raw: Path, manifest: dict, force: bool) -> None:
    src = cfg["sources"]["cambridge"]
    base = f"https://raw.githubusercontent.com/{src['repo']}/{src['commit']}/"
    for name, rel in src["layers"].items():
        out = raw / f"{name}.geojson"
        url = base + rel
        if force or not out.exists():
            print(f"  cambridge/{name} ...", flush=True)
            out.write_bytes(_get(url))
        _record(manifest, name, out, url)


def _query_tile(url: str, town_id: int, tile) -> list[dict]:
    """All non-Cambridge structures intersecting one lon/lat tile, paged."""
    feats, offset, page = [], 0, 2000
    while True:
        params = {
            "where": f"TOWN_ID <> {town_id}",
            "geometry": ",".join(f"{v:.6f}" for v in tile),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "OBJECTID,STRUCT_ID,TOWN_ID,AREA_SQ_FT",
            "outSR": 4326,
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": page,
            "f": "geojson",
        }
        data = json.loads(_get(url + "?" + urllib.parse.urlencode(params)))
        if "error" in data:
            raise RuntimeError(f"MassGIS query failed for tile {tile}: {data['error']}")
        batch = data.get("features", [])
        feats += batch
        if len(batch) < page:
            return feats
        offset += page


def fetch_neighbor_buildings(cfg: dict, raw: Path, manifest: dict, force: bool) -> None:
    src = cfg["sources"]["massgis_buildings"]
    out = raw / "neighbor_buildings.geojson"
    if out.exists() and not force:
        _record(manifest, "neighbor_buildings", out, src["url"])
        return

    crs = cfg["project"]["crs"]
    city = gpd.read_file(raw / "boundary.geojson").to_crs(crs).union_all()
    ring = city.buffer(src["ring_ft"]).difference(city)
    ring_ll = gpd.GeoSeries([ring], crs=crs).to_crs(4326).iloc[0]

    step = src["tile_deg"]
    x0, y0, x1, y1 = ring_ll.bounds
    tiles = []
    y = y0
    while y < y1:
        x = x0
        while x < x1:
            t = (x, y, min(x + step, x1), min(y + step, y1))
            if box(*t).intersects(ring_ll):
                tiles.append(t)
            x += step
        y += step

    print(f"  massgis/neighbor_buildings: {len(tiles)} tiles ...", flush=True)
    with ThreadPoolExecutor(src["workers"]) as pool:
        results = list(pool.map(lambda t: _query_tile(src["url"], src["cambridge_town_id"], t), tiles))

    feats = [f for r in results for f in r]
    gdf = gpd.GeoDataFrame.from_features(feats, crs=4326)
    gdf = gdf.drop_duplicates("OBJECTID")
    gdf = gdf[gdf.to_crs(crs).intersects(ring)]
    gdf.to_file(out, driver="GeoJSON")
    print(f"    {len(gdf)} structures within {src['ring_ft']} ft of the city line")
    _record(manifest, "neighbor_buildings", out, src["url"])


def run(cfg: dict, force: bool = False) -> None:
    raw = cfg["project"]["raw_dir"]
    raw.mkdir(parents=True, exist_ok=True)
    manifest_path = raw / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    print("fetch")
    fetch_cambridge(cfg, raw, manifest, force)
    fetch_neighbor_buildings(cfg, raw, manifest, force)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  manifest -> {manifest_path.relative_to(cfg['project']['raw_dir'].parent.parent)}")
