"""Run the constraint stack for each Massachusetts town from prepared.gpkg.

Per town, in EPSG:26986 meters:
  1. read every layer within the largest setback of the town line
  2. build exclusions: setback buffers and hard roads + 150 ft (Massachusetts law),
     open water and public recreation land (assumptions), Zone A if switched on
  3. per scenario: state result = town - setback - everything else, minus slivers
  4. town ordinances on top, for towns with a [local] block: the final result
  5. split candidates by who decides (MassWildlife, other public, land trust or
     nonprofit, private) and attach open-space site names

Outputs land in data/massgis/out/: one GeoPackage per town (full resolution),
one compact JSON per town for the web map, and summary.csv for the state.
"""

import csv
import json
import math
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from .config import FT

SQM_PER_ACRE = 4046.8564224
CATS = ["dfg", "public", "conservation", "private"]
QUAD_SEGS = 4
# A 4-segments-per-quarter buffer is inscribed in the true circle. Growing the
# distance by 1/cos(pi/16) makes it circumscribe instead, so the error always
# shrinks candidates (by at most ~2%) rather than letting someone stand too close.
CIRCUMSCRIBE = 1 / math.cos(math.pi / (4 * QUAD_SEGS))


def _acres(g) -> float:
    return 0.0 if g is None or g.is_empty else g.area / SQM_PER_ACRE


def _union(geoms) -> shapely.Geometry:
    arr = np.asarray([g for g in geoms if g is not None and not g.is_empty], dtype=object)
    return shapely.union_all(arr) if len(arr) else shapely.Polygon()


def _polys(g) -> shapely.Geometry:
    parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]
    return shapely.MultiPolygon(parts) if parts else shapely.Polygon()


def _buffer_union(geoms: np.ndarray, dist_m) -> shapely.Geometry:
    if len(geoms) == 0:
        return shapely.Polygon()
    return shapely.union_all(shapely.buffer(geoms, np.asarray(dist_m) * CIRCUMSCRIBE, quad_segs=QUAD_SEGS))


def _read(gpkg, layer, bbox, where=None, columns=None):
    return pyogrio.read_dataframe(gpkg, layer=layer, bbox=bbox, where=where, columns=columns)


# ---------------------------------------------------------------- one town

def _load(cfg, town_id):
    gpkg = cfg["project"]["work_gpkg"]
    town = pyogrio.read_dataframe(gpkg, layer="towns", where=f"TOWN_ID = {town_id}")
    T = shapely.make_valid(_union(town.geometry))
    reach = max(s["setback_ft"] for s in cfg["scenario"]) * FT * CIRCUMSCRIBE + 50
    x0, y0, x1, y1 = T.bounds
    bbox = (x0 - reach, y0 - reach, x1 + reach, y1 + reach)
    near = T.buffer(reach)
    d = {"town": town.iloc[0], "T": T, "near": near, "bbox": bbox}
    for layer in ("structures", "roads", "water", "zone_a", "openspace", "parcels"):
        cols = {"parcels": ["owner_cat", "row"]}.get(layer)
        gdf = _read(gpkg, layer, bbox, columns=cols)
        gdf = gdf[gdf.intersects(near)] if len(gdf) else gdf
        d[layer] = gdf
    return d


def _exclusions(cfg, d) -> dict:
    T, rules = d["T"], cfg["rules"]
    roads = d["roads"]
    hard = roads["hard"].astype(bool).values
    hw = roads["half_w_m"].values
    road_parts = [_buffer_union(roads.geometry.values[hard], hw[hard] + rules["roads"]["hard_surface_setback_ft"] * FT)]
    if rules["roads"]["exclude_unpaved_surface"]:
        road_parts.append(_buffer_union(roads.geometry.values[~hard], hw[~hard]))
    road_geom = _union(road_parts)
    os_ = d["openspace"]
    parks = _union(os_[os_["park"].astype(bool)].geometry.values)
    water = _union(d["water"].geometry.values)
    if rules["water"]["exclude_zone_a"]:
        water = _union([water, _union(d["zone_a"].geometry.values)])
    return {k: _polys(shapely.intersection(g, T)) for k, g in
            {"parks": parks, "roads": road_geom, "water": water}.items()}


def _setback_sources(cfg, d) -> dict:
    s, b = d["structures"], cfg["rules"]["buildings"]
    known = s["parcel_known"].astype(bool)
    is_dw = s["parcel_dwelling"].astype(bool) | (~known & b["unknown_is_dwelling"])
    return {
        "buildings": s[s["area_sqft"] >= b["min_footprint_sqft"]].geometry.values,
        "dwellings": s[is_dw & (s["area_sqft"] >= b["dwelling_min_footprint_sqft"])].geometry.values,
    }


def _category_areas(cfg, d, extent) -> dict:
    """Disjoint polygons for each 'who decides' category, clipped to extent."""
    os_, par = d["openspace"], d["parcels"]
    par = par[~par["row"].astype(bool)]
    dfg = _union([_union(os_[os_["cat"] == "dfg"].geometry.values), _union(par[par["owner_cat"] == "dfg"].geometry.values)])
    pub = _union([_union(os_[os_["cat"] == "public"].geometry.values), _union(par[par["owner_cat"] == "public"].geometry.values)])
    con = _union(os_[os_["cat"] == "conservation"].geometry.values)
    # Overlays can return stray lines and points alongside polygons; GEOS then refuses
    # the next overlay ("unable to determine result dimension"), so keep polygons only.
    out, taken = {}, shapely.Polygon()
    for name, g in (("dfg", dfg), ("public", pub), ("conservation", con)):
        g = _polys(_polys(shapely.intersection(_polys(g), extent)).difference(taken))
        out[name] = g
        taken = _polys(_union([taken, g]))
    out["private"] = _polys(extent.difference(taken))
    return out


def _pieces(cand, cats, os_) -> gpd.GeoDataFrame:
    """Candidate area split into category pieces, each with an open-space site name if it has one."""
    rows = []
    for cat in CATS:
        g = shapely.intersection(cand, cats[cat]) if not cats[cat].is_empty else shapely.Polygon()
        for p in shapely.get_parts(_polys(g)):
            rows.append({"cat": cat, "geometry": p})
    if not rows:
        return gpd.GeoDataFrame({"cat": [], "site": [], "acres": []}, geometry=[], crs=26986)
    pieces = gpd.GeoDataFrame(rows, crs=26986)
    named = os_[os_["site"].fillna("").str.strip() != ""][["site", "geometry"]]
    if len(named):
        pts = gpd.GeoDataFrame(geometry=pieces.representative_point(), crs=26986)
        j = gpd.sjoin(pts, named, predicate="within", how="left")
        pieces["site"] = j.groupby(level=0)["site"].first()
    else:
        pieces["site"] = None
    pieces["acres"] = pieces.area / SQM_PER_ACRE
    return pieces


def analyze_town(cfg: dict, town_id: int) -> dict:
    t0 = time.time()
    d = _load(cfg, town_id)
    T, town = d["T"], d["town"]
    local = cfg.get("local", {}).get(str(town_id), {})
    min_sqm = cfg["project"]["min_candidate_sqft"] * FT ** 2

    excl = _exclusions(cfg, d)
    base = _polys(T.difference(_union(excl.values())))
    sources = _setback_sources(cfg, d)
    setback_cache = {}
    # Local street ban: every road surface. Hard roads are already erased with their
    # 150 ft, so only the unpaved surfaces can add anything.
    streets = shapely.Polygon()
    if local.get("street_ban"):
        r = d["roads"]
        soft = ~r["hard"].astype(bool).values
        streets = _polys(shapely.intersection(_buffer_union(r.geometry.values[soft], r["half_w_m"].values[soft]), T))

    def no_slivers(g):
        return _polys(shapely.MultiPolygon([p for p in shapely.get_parts(g) if p.area >= min_sqm]))

    row = {"town_id": town_id, "town": town["TOWN"], "county": town["COUNTY"], "town_acres": _acres(T),
           "n_structures": len(d["structures"]), "n_dwellings": len(sources["dwellings"]),
           "local_rules": bool(local)}
    cands = {}
    for sc in cfg["scenario"]:
        sid = sc["id"]
        key = (sc["setback_from"], sc["setback_ft"])
        if key not in setback_cache:
            setback_cache[key] = _polys(shapely.intersection(_buffer_union(sources[sc["setback_from"]], sc["setback_ft"] * FT), T))
        X = setback_cache[key]
        state = no_slivers(base.difference(X))
        if sc["weapon"] == "firearm" and local.get("firearm_ban"):
            cand = shapely.Polygon()
        elif sc["weapon"] == "archery" and not streets.is_empty:
            cand = no_slivers(state.difference(streets))
        else:
            cand = state
        cands[sid] = cand
        row[f"{sid}_outside_setback_ac"] = _acres(T) - _acres(X)
        row[f"{sid}_state_ac"] = _acres(state)       # Massachusetts law + assumptions
        row[f"{sid}_ac"] = _acres(cand)              # ...plus the town's own rules

    # Who decides: one category split shared by every scenario (computed on the widest one).
    widest = _union(cands.values())
    cats = _category_areas(cfg, d, widest)
    out_dir = Path(cfg["project"]["out_dir"])
    gpkg = out_dir / "towns" / f"{town_id:03d}.gpkg"
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    gpkg.unlink(missing_ok=True)
    per_scenario = {}
    for sid, cand in cands.items():
        pieces = _pieces(cand, cats, d["openspace"])
        per_scenario[sid] = pieces
        for cat in CATS:
            row[f"{sid}_{cat}_ac"] = float(pieces.loc[pieces["cat"] == cat, "acres"].sum()) if len(pieces) else 0.0
        if len(pieces):
            pieces.assign(town_id=town_id, scenario=sid).to_file(gpkg, layer=f"candidates_{sid}", driver="GPKG")

    _write_web(cfg, town_id, d, per_scenario, excl)
    row["seconds"] = round(time.time() - t0, 1)
    return row


# ---------------------------------------------------------------- web output

def _round_coords(geojson_geom: dict, nd: int = 5):
    def r(c):
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [r(x) for x in c]
    return {"type": geojson_geom["type"], "coordinates": r(geojson_geom["coordinates"])}


def _to_web(geoms: list, props: list, tol_m: float) -> list:
    if not geoms:
        return []
    s = gpd.GeoSeries(geoms, crs=26986).simplify(tol_m, preserve_topology=True).to_crs(4326)
    feats = []
    for g, p in zip(s, props):
        if g is None or g.is_empty:
            continue
        feats.append({"type": "Feature", "properties": p, "geometry": _round_coords(shapely.geometry.mapping(g))})
    return feats


def _write_web(cfg, town_id, d, per_scenario, excl) -> None:
    """Nested tiers keep each acre once: tier k = scenario k minus scenario k-1."""
    w = cfg.get("web", {})
    order = w.get("tiers", [s["id"] for s in cfg["scenario"]])
    tol = w.get("simplify_m", 3)
    feats, prev = [], shapely.Polygon()
    for tier, sid in enumerate(order):
        pieces = per_scenario.get(sid)
        if pieces is None or not len(pieces):
            continue
        for (cat, site), grp in pieces.fillna({"site": ""}).groupby(["cat", "site"]):
            g = _polys(_union(grp.geometry.values).difference(prev)) if not prev.is_empty else _union(grp.geometry.values)
            if g.is_empty or g.area < 1:
                continue
            feats += _to_web([g], [{"t": tier, "c": cat, "s": site or None, "a": round(_acres(g), 2)}], tol)
        prev = _union([prev, _union(pieces.geometry.values)])

    T = d["T"]
    roads = d["roads"]
    roads = roads[roads.intersects(T)]
    ctx = []
    for cls, sel in (("major", roads["rdtype"] <= 4), ("local", roads["rdtype"] >= 5)):
        g = shapely.intersection(shapely.line_merge(_union(roads[sel].geometry.values)), T)
        ctx += _to_web([g], [{"k": cls}], 4)
    water = shapely.intersection(_union(d["water"].geometry.values), T)
    ctx += _to_web([water], [{"k": "water"}], 4)

    town = d["town"]
    out = {"id": town_id, "name": town["TOWN"], "tiers": order,
           "bbox": [round(v, 5) for v in gpd.GeoSeries([T], crs=26986).to_crs(4326).total_bounds],
           "candidates": feats, "context": ctx}
    path = Path(cfg["project"]["out_dir"]) / "web" / f"{town_id:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")))


# ---------------------------------------------------------------- driver

def _resolve_towns(cfg, towns: str | None) -> list[int]:
    t = pyogrio.read_dataframe(cfg["project"]["work_gpkg"], layer="towns", columns=["TOWN", "TOWN_ID"], read_geometry=False)
    if not towns:
        return sorted(t["TOWN_ID"].astype(int))
    by_name = {n.lower(): int(i) for n, i in zip(t["TOWN"], t["TOWN_ID"])}
    ids = []
    for tok in towns.split(","):
        tok = tok.strip()
        ids.append(int(tok) if tok.isdigit() else by_name[tok.lower()])
    return ids


def _safe(args):
    cfg, tid = args
    try:
        return analyze_town(cfg, tid)
    except Exception:
        return {"town_id": tid, "error": traceback.format_exc()}


def run(cfg: dict, towns: str | None = None) -> None:
    ids = _resolve_towns(cfg, towns)
    out_dir = Path(cfg["project"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.csv"
    prev = {}
    if summary_path.exists():
        with open(summary_path) as f:
            prev = {int(r["town_id"]): r for r in csv.DictReader(f)}

    print(f"ma run: {len(ids)} towns, {cfg['project']['workers']} workers", flush=True)
    t0, rows, errors = time.time(), [], []
    # Big towns first so the long poles start early.
    areas = pyogrio.read_dataframe(cfg["project"]["work_gpkg"], layer="towns", columns=["TOWN_ID", "POP2020"], read_geometry=False)
    pop = dict(zip(areas["TOWN_ID"].astype(int), areas["POP2020"]))
    ids.sort(key=lambda i: -pop.get(i, 0))
    with ProcessPoolExecutor(cfg["project"]["workers"]) as pool:
        futs = [pool.submit(_safe, (cfg, i)) for i in ids]
        for n, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            (errors if "error" in r else rows).append(r)
            if "error" not in r and (n % 25 == 0 or len(ids) <= 25):
                print(f"  {n}/{len(ids)} {r['town']}: archery 500 ft {r.get('archery_500ft_ac', 0):,.0f} ac "
                      f"({r['seconds']} s)", flush=True)
    for e in errors:
        print(f"  ERROR town {e['town_id']}:\n{e['error']}")

    for r in rows:
        prev[r["town_id"]] = r
    fields = list(rows[0].keys()) if rows else list(next(iter(prev.values())).keys())
    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for tid in sorted(prev):
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in prev[tid].items()})
    print(f"  done in {time.time() - t0:.0f} s, {len(errors)} errors -> {summary_path}")
