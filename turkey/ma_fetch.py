"""Download statewide MassGIS layers and per-town L3 parcels into data/raw/massgis.

Each archive is unzipped into its own directory. manifest.json records what came
from where, with hashes, so a later run can tell what changed.
"""

import hashlib
import json
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .fetch import _get

STATE_LAYERS = ("towns", "structures", "openspace", "hydro", "roads", "zone_a")


def _valid_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return z.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


def _download_zip(url: str, dest: Path, force: bool) -> dict:
    """Download + extract once. `.ok` is written only after a complete extract,
    so an interrupted run is redone on the next call instead of trusted."""
    dest.mkdir(parents=True, exist_ok=True)
    zpath = dest / Path(urllib.parse.urlparse(url).path).name
    ok = dest / ".ok"
    fetched = None
    if force or not ok.exists():
        if force or not (zpath.exists() and _valid_zip(zpath)):
            tmp = zpath.with_suffix(".part")
            tmp.write_bytes(_get(url, timeout=1800))
            tmp.replace(zpath)
            fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(dest)
        ok.write_text(url)
    h = hashlib.sha256()
    with open(zpath, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"url": url, "file": str(zpath), "bytes": zpath.stat().st_size, "sha256": h.hexdigest(), "fetched_utc": fetched}


def _query_all(url: str, where: str = "1=1", out_fields: str = "*", geometry: bool = True) -> dict:
    """Page through an ArcGIS feature layer; returns one GeoJSON FeatureCollection."""
    feats, offset = [], 0
    while True:
        params = {"where": where, "outFields": out_fields, "returnGeometry": str(geometry).lower(),
                  "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": 1000,
                  "outSR": 26986, "f": "geojson"}
        data = json.loads(_get(url + "?" + urllib.parse.urlencode(params)))
        if "error" in data:
            raise RuntimeError(f"{url}: {data['error']}")
        batch = data.get("features", [])
        feats += batch
        if len(batch) < 1000:
            return {"type": "FeatureCollection", "features": feats}
        offset += 1000


def run(cfg: dict, force: bool = False) -> None:
    src = cfg["sources"]
    raw = Path(cfg["project"]["raw_dir"])
    manifest_path = raw / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    def record(key, entry):
        prev = manifest.get(key, {})
        if entry["fetched_utc"] is None:
            entry["fetched_utc"] = prev.get("fetched_utc") if prev.get("sha256") == entry["sha256"] else \
                datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest[key] = entry

    print("ma fetch")
    jobs = [(f"state/{k}", f"{src['s3']}/{src[k]}", raw / "state" / k) for k in STATE_LAYERS]

    index = _query_all(src["parcel_index"], out_fields="TOWN,TOWN_ID,FY,SHAPE_LINK", geometry=False)
    towns = sorted((f["properties"] for f in index["features"]), key=lambda p: p["TOWN_ID"])
    (raw / "parcel_index.json").write_text(json.dumps(towns, indent=1))
    jobs += [(f"parcels/{t['TOWN_ID']:03d}", t["SHAPE_LINK"], raw / "parcels" / f"{t['TOWN_ID']:03d}") for t in towns]

    todo = [j for j in jobs if force or not (j[2] / ".ok").exists()]
    print(f"  {len(jobs)} archives ({len(jobs) - len(todo)} already here), downloading {len(todo)} ...", flush=True)
    done = 0
    with ThreadPoolExecutor(src["download_workers"]) as pool:
        futures = {pool.submit(_download_zip, url, dest, force): key for key, url, dest in jobs}
        for fut, key in futures.items():
            record(key, fut.result())
            done += 1
            if done % 50 == 0:
                print(f"    {done}/{len(jobs)}", flush=True)

    manifest_path.write_text(json.dumps(manifest, indent=1))
    total = sum(v["bytes"] for v in manifest.values()) / 1e9
    print(f"  {len(manifest)} sources, {total:.1f} GB -> {manifest_path}")
