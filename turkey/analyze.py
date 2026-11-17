"""Stack the constraint layers, erase them from the city, and tag what survives by owner.

Each rule becomes one exclusion geometry (the "map draws" column of slide 3).
The waterfall erases them in config order; candidates are what's left.
"""

import csv

import geopandas as gpd
import pandas as pd
import shapely

from .config import SQFT_PER_ACRE

RULE_ORDER = ["dwellings", "parks", "roads", "water"]


def _read(cfg: dict, name: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(cfg["project"]["raw_dir"] / f"{name}.geojson").to_crs(cfg["project"]["crs"])
    gdf["geometry"] = shapely.make_valid(gdf.geometry.values)
    return gdf[~gdf.geometry.is_empty]


def _union(geoms) -> shapely.Geometry:
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    return shapely.union_all(geoms) if geoms else shapely.Polygon()


def _acres(geom) -> float:
    return geom.area / SQFT_PER_ACRE


def _polys(geom) -> shapely.Geometry:
    """Drop the stray lines/points that make_valid and difference can leave behind."""
    parts = [g for g in shapely.get_parts(geom) if g.geom_type in ("Polygon", "MultiPolygon")]
    return _union(parts)


# ---------------------------------------------------------------- inputs

def load_inputs(cfg: dict) -> dict:
    print("  loading layers ...", flush=True)
    d = {name: _read(cfg, name) for name in
         ("boundary", "buildings", "land_use", "open_space", "roads", "bridges", "water", "neighbor_buildings")}
    d["city"] = _union(d["boundary"].geometry)
    return d


def dwelling_footprints(cfg: dict, d: dict) -> gpd.GeoDataFrame:
    r = cfg["rules"]["dwellings"]
    lu = d["land_use"]
    residential = lu[(lu["residential_units"].fillna(0) > 0)
                     | (lu["group_quarters"].astype(str) == "1")
                     | lu["use_category"].isin(r["overnight_use_categories"])]

    bldg = d["buildings"][d["buildings"]["TYPE"].isin(r["cambridge_building_types"])].copy()
    pts = bldg.copy()
    pts["geometry"] = bldg.geometry.representative_point()
    hits = gpd.sjoin(pts, residential[["geometry"]], predicate="within", how="inner")
    camb = bldg.loc[hits.index.unique()].assign(source="Cambridge (residential or overnight-use parcel)")

    nb = d["neighbor_buildings"]
    nb = nb[nb.geometry.area >= r["neighbor_min_footprint_sqft"]].assign(source="MassGIS (neighbor town, all structures)")

    out = pd.concat([camb[["source", "geometry"]], nb[["source", "geometry"]]], ignore_index=True)
    return gpd.GeoDataFrame(out, crs=cfg["project"]["crs"])


def static_exclusions(cfg: dict, d: dict) -> dict:
    """Exclusion geometries that don't change between scenarios."""
    city = d["city"]
    rules = cfg["rules"]

    os_ = d["open_space"]
    owner = os_["OWNER"].fillna("").str.strip()
    parks = _union(os_[owner.isin([o.strip() for o in rules["parks"]["excluded_owners"]])].geometry)

    rr = rules["roads"]
    roads = d["roads"]
    streets = _union(roads[roads["TYPE"].isin(rr["street_types"])].geometry)
    hard = _union(roads[roads["TYPE"].isin(rr["hard_surface_types"])].geometry)
    road_excl = [streets, hard.buffer(rr["hard_surface_setback_ft"])]
    if rr["include_bridges_layer"]:
        road_excl.append(_union(d["bridges"].geometry))
    roads_geom = _union(road_excl)

    rw = rules["water"]
    water = d["water"]
    reservoirs = water[water["NAME"].isin(rw["reservoir_names"])]
    water_geom = _union([_union(water.geometry), _union(reservoirs.geometry).buffer(rw["reservoir_buffer_ft"])])

    return {k: _polys(g.intersection(city)) for k, g in
            {"parks": parks, "roads": roads_geom, "water": water_geom}.items()}


# ---------------------------------------------------------------- per scenario

def waterfall(city, layers: dict, citywide_ban: bool) -> tuple[list[dict], shapely.Geometry]:
    rows = [{"step": "Cambridge city limits", "acres_remaining": _acres(city), "acres_removed": 0.0}]
    remaining = shapely.Polygon() if citywide_ban else city
    if citywide_ban:
        rows.append({"step": "citywide discharge ban", "acres_remaining": 0.0, "acres_removed": _acres(city)})
    for name in RULE_ORDER:
        before = _acres(remaining)
        remaining = _polys(remaining.difference(layers[name]))
        rows.append({"step": name, "acres_remaining": _acres(remaining), "acres_removed": before - _acres(remaining)})

    # "Only this rule" = acres that would come back if this one rule alone were dropped.
    # Under a citywide ban every later rule is moot, so the column stays blank.
    for row in rows:
        name = row["step"]
        if citywide_ban or name not in layers:
            row["acres_only_this_rule"] = None
            continue
        others = _union([g for k, g in layers.items() if k != name])
        row["acres_only_this_rule"] = _acres(city.difference(others)) - _acres(remaining)
    return rows, remaining


def candidate_parcels(cfg: dict, candidates: gpd.GeoDataFrame, d: dict) -> gpd.GeoDataFrame:
    """Split candidates by land-use parcel so each piece names who has to say yes."""
    perm = cfg["rules"]["permission"]
    lu = d["land_use"][["ml", "location", "use_category", "tax_status", "geometry"]]
    pieces = gpd.overlay(candidates[["candidate_id", "geometry"]], lu, how="intersection", keep_geom_type=True)
    pieces["owner_class"] = pieces["tax_status"].map(perm["owner_class"]).fillna(perm["default_owner_class"])

    os_ = d["open_space"][["NAME", "geometry"]].rename(columns={"NAME": "open_space_name"})
    j = gpd.sjoin(pieces.assign(geometry=pieces.representative_point()), os_, predicate="within", how="left")
    pieces["open_space_name"] = j.groupby(level=0)["open_space_name"].first()

    pieces["acres"] = pieces.area / SQFT_PER_ACRE
    pieces = pieces[pieces["acres"] > 0.01]
    return pieces.sort_values("acres", ascending=False).reset_index(drop=True)


def run(cfg: dict) -> None:
    print("analyze")
    crs = cfg["project"]["crs"]
    gpkg = cfg["project"]["gpkg"]
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    gpkg.unlink(missing_ok=True)

    d = load_inputs(cfg)
    city = d["city"]

    def write(geom_or_gdf, layer: str) -> None:
        gdf = geom_or_gdf if isinstance(geom_or_gdf, gpd.GeoDataFrame) else \
            gpd.GeoDataFrame({"layer": [layer]}, geometry=[geom_or_gdf], crs=crs)
        gdf.to_file(gpkg, layer=layer, driver="GPKG")

    write(city, "city_limits")
    dwellings = dwelling_footprints(cfg, d)
    write(dwellings, "dwelling_footprints")
    print(f"  dwellings: {(dwellings.source.str.startswith('Cambridge')).sum()} Cambridge + "
          f"{(~dwellings.source.str.startswith('Cambridge')).sum()} neighbor-town structures", flush=True)
    # Buffer each footprint, then union: same result as buffering the union (to arc
    # rounding) but ~30x faster than buffering one 277k-vertex multipolygon.
    def dwelling_setback(ft):
        return _polys(_union(shapely.buffer(dwellings.geometry.values, ft)).intersection(city))

    print("  building static exclusions (parks, roads, water) ...", flush=True)
    static = static_exclusions(cfg, d)
    for k, g in static.items():
        write(g, f"excl_{k}")

    summary = []
    for sc in cfg["scenario"]:
        sid = sc["id"]
        ban = sc.get("citywide_ban", False)
        print(f"  scenario {sid} ...", flush=True)
        layers = dict(static)
        layers["dwellings"] = shapely.Polygon() if ban else dwelling_setback(sc["dwelling_setback_ft"])
        if not ban:
            write(layers["dwellings"], f"excl_dwellings_{sid}")

        rows, remaining = waterfall(city, layers, ban)
        cand = gpd.GeoDataFrame(geometry=list(shapely.get_parts(remaining)), crs=crs)
        n_raw = len(cand)
        cand = cand[cand.area >= cfg["project"]["min_candidate_sqft"]].reset_index(drop=True)
        cand["candidate_id"] = [f"{sid}-{i + 1:02d}" for i in range(len(cand))]
        cand["acres"] = cand.area / SQFT_PER_ACRE
        rows.append({"step": f"slivers under {cfg['project']['min_candidate_sqft']} sq ft",
                     "acres_remaining": cand["acres"].sum(),
                     "acres_removed": rows[-1]["acres_remaining"] - cand["acres"].sum(),
                     "acres_only_this_rule": None})
        for r in rows:
            summary.append({"scenario": sid, **r})

        if len(cand):
            write(cand, f"candidates_{sid}")
            parcels = candidate_parcels(cfg, cand, d)
            write(parcels, f"candidate_parcels_{sid}")
            by_owner = parcels.groupby("owner_class")["acres"].sum().sort_values(ascending=False)
            print(f"    {len(cand)} candidate areas ({n_raw - len(cand)} slivers dropped), "
                  f"{cand['acres'].sum():.1f} ac; by owner: " +
                  ", ".join(f"{k} {v:.1f}" for k, v in by_owner.items()))
        else:
            print("    0 candidate acres")

    out_csv = cfg["project"]["out_dir"] / "summary.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scenario", "step", "acres_remaining", "acres_removed", "acres_only_this_rule"])
        w.writeheader()
        for r in summary:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    print(f"  -> {gpkg.name}, {out_csv.name}")
