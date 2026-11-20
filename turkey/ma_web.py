"""Assemble the static web map in web/ from the per-town outputs.

web/
  src/index.html      the hand-written page (template)
  index.html          the page with Leaflet's stylesheet inlined (built)
  data/towns.json     statewide town outlines + per-scenario numbers (overview)
  data/context.json   statewide major roads and large water bodies
  data/t/NNN.json     per-town candidates and local context, loaded on click

Geometry in data/ uses a compact encoding instead of GeoJSON coordinates, about
3x smaller: each feature has "k" (P = multipolygon, L = multiline) and "g", where
every ring or line is a flat list of integers in 1e-5 degrees, the first pair
absolute and each later pair the change from the previous point. The page
decodes it back to GeoJSON (see decode() in web/src/index.html).
"""

import csv
import json
from pathlib import Path

import geopandas as gpd
import pyogrio
import shapely

from .ma_analyze import SQM_PER_ACRE

Q = 1e5  # 1e-5 degree grid: ~1.1 m north-south, ~0.8 m east-west in Massachusetts
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"


def _enc_line(coords) -> list[int]:
    out, px, py = [], None, None
    for x, y in ((round(c[0] * Q), round(c[1] * Q)) for c in coords):
        if px is None:
            out += [x, y]
        elif (x, y) != (px, py):
            out += [x - px, y - py]
        px, py = x, y
    return out


def encode(geom: dict) -> dict | None:
    """GeoJSON geometry dict -> {"k": "P"|"L", "g": [...]}; None if nothing survives."""
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        t, c = "MultiPolygon", [c]
    elif t == "LineString":
        t, c = "MultiLineString", [c]
    if t == "MultiPolygon":
        polys = [[r for r in (_enc_line(ring) for ring in poly) if len(r) >= 8] for poly in c]
        polys = [p for p in polys if p]
        return {"k": "P", "g": polys} if polys else None
    if t == "MultiLineString":
        lines = [r for r in (_enc_line(line) for line in c) if len(r) >= 4]
        return {"k": "L", "g": lines} if lines else None
    if t == "GeometryCollection":
        # Keep the polygon parts if there are any, else the line parts; drop stray points.
        parts = [p for p in (encode(g) for g in geom["geometries"]) if p]
        for kind in ("P", "L"):
            same = [p["g"] for p in parts if p["k"] == kind]
            if same:
                return {"k": kind, "g": [x for g in same for x in g]}
    return None


def _encode_features(feats: list) -> list:
    out = []
    for f in feats:
        e = encode(f["geometry"])
        if e:
            out.append({**f["properties"], **e})
    return out


def _gdf_features(gdf: gpd.GeoDataFrame, props: list[str], tol_m: float) -> list:
    g = gdf.copy()
    g["geometry"] = g.geometry.simplify(tol_m, preserve_topology=True)
    g = g[~g.geometry.is_empty].to_crs(4326)
    return _encode_features([{"properties": {k: r[k] for k in props}, "geometry": shapely.geometry.mapping(r.geometry)}
                             for _, r in g.iterrows()])


def render_page(cfg: dict) -> None:
    """web/src/index.html -> web/index.html with Leaflet's stylesheet inlined
    (the artifact host only loads scripts from CDNs, not stylesheets)."""
    from .fetch import _get
    web = Path(cfg["project"]["web_dir"])
    css_path = Path(cfg["project"]["raw_dir"]).parent / "vendor" / "leaflet-1.9.4.css"
    if not css_path.exists():
        css_path.parent.mkdir(parents=True, exist_ok=True)
        css_path.write_bytes(_get(LEAFLET_CSS))
    page = (web / "src" / "index.html").read_text().replace("{{LEAFLET_CSS}}", css_path.read_text())
    (web / "index.html").write_text(page)


def _dump(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def run(cfg: dict) -> None:
    print("ma web")
    work = cfg["project"]["work_gpkg"]
    out = Path(cfg["project"]["out_dir"])
    data = Path(cfg["project"]["web_dir"]) / "data"
    (data / "t").mkdir(parents=True, exist_ok=True)

    with open(out / "summary.csv") as f:
        rows = {int(r["town_id"]): r for r in csv.DictReader(f)}
    sids = [s["id"] for s in cfg["scenario"]]
    cats = ["dfg", "public", "conservation", "private"]

    towns = pyogrio.read_dataframe(work, layer="towns")
    towns = towns.dissolve(by="TOWN_ID", aggfunc="first").reset_index()
    local = cfg.get("local", {})
    recs = []
    for _, t in towns.iterrows():
        tid = int(t["TOWN_ID"])
        r = rows.get(tid)
        rec = {"id": tid, "name": t["TOWN"], "county": str(t["COUNTY"]).title(),
               "ac": round(t.geometry.area / SQM_PER_ACRE), "done": r is not None,
               "local": local.get(str(tid), {}).get("note")}
        if r:
            for sid in sids:
                rec[sid] = {"ac": round(float(r[f"{sid}_ac"]), 1),
                            **{c: round(float(r[f"{sid}_{c}_ac"]), 1) for c in cats}}
                if rec["local"]:
                    rec[sid]["st"] = round(float(r[f"{sid}_state_ac"]), 1)   # before the town's own rules
        recs.append(rec)
    towns = towns.rename(columns={"TOWN_ID": "id"})
    towns["id"] = towns["id"].astype(int)
    _dump({"towns": recs, "geo": _gdf_features(towns, ["id"], tol_m=60),
           "scenarios": [{"id": s["id"], "label": s["label"]} for s in cfg["scenario"]]}, data / "towns.json")

    roads = pyogrio.read_dataframe(work, layer="roads", where="rdtype <= 2", columns=["rdtype"])
    roads = gpd.GeoDataFrame({"k2": ["major"]}, geometry=[shapely.line_merge(roads.union_all())], crs=roads.crs)
    # Inland lakes, ponds and reservoirs only: ocean polygons would draw the 3-mile line offshore.
    water = pyogrio.read_dataframe(work, layer="water", columns=["POLY_CODE"], where="POLY_CODE IN (1, 6)")
    water = water[water.area > 400 * SQM_PER_ACRE]
    water = gpd.GeoDataFrame({"k2": ["water"]}, geometry=[water.union_all()], crs=water.crs)
    ctx = gpd.GeoDataFrame(gpd.pd.concat([roads, water]), crs=roads.crs).rename(columns={"k2": "w"})
    _dump(_gdf_features(ctx, ["w"], tol_m=80), data / "context.json")

    render_page(cfg)

    n = 0
    for src in sorted((out / "web").glob("*.json")):
        town = json.loads(src.read_text())
        town["candidates"] = _encode_features(town["candidates"])
        # context features carry their kind in "k" in the analysis output; the compact
        # encoding uses "k" for geometry type, so the kind moves to "w".
        town["context"] = _encode_features([{**f, "properties": {"w": f["properties"]["k"]}} for f in town["context"]])
        _dump(town, data / "t" / src.name)
        n += 1
    sizes = sorted(((p.stat().st_size, p.name) for p in (data / "t").glob("*.json")), reverse=True)
    total = sum(s for s, _ in sizes) + (data / "towns.json").stat().st_size + (data / "context.json").stat().st_size
    print(f"  towns.json {(data / 'towns.json').stat().st_size / 1e6:.2f} MB, context.json "
          f"{(data / 'context.json').stat().st_size / 1e6:.2f} MB, {n} town files, total {total / 1e6:.0f} MB")
    if sizes:
        print("  largest town files: " + ", ".join(f"{name} {s / 1e6:.2f} MB" for s, name in sizes[:5]))
