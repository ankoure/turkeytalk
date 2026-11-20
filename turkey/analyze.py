"""Stack the constraint layers, erase them from the city, and tag what survives by owner.

Each rule becomes one exclusion geometry (the "map draws" column of slide 3), and
the waterfall erases them in tiers so it reads as an argument:

  land    open water
  state   Massachusetts law: the setback (MGL c.131 §58 from dwellings, or c.269 §12E
          from any building for firearms) and 150 ft from hard-surfaced highways (§58)
  access  not law: public parkland, where the owner is assumed to say no
  local   Cambridge ordinances, on top: no firearm discharge (§9.16.010) or no bows in
          streets and on bridges (§9.16.060)

What's left before the local tier is what Massachusetts allows in Cambridge (the
statewide map's question); what's left after it is the Cambridge answer.
"""

import csv

import geopandas as gpd
import pandas as pd
import shapely

from .config import SQFT_PER_ACRE

# Waterfall order. "setback" and "local" are built per scenario; the rest are shared.
STEPS = [("water", "land"), ("setback", "state"), ("highways", "state"), ("parks", "access"), ("local", "local")]
TIER_LABEL = {"land": "Dry land", "state": "Massachusetts law", "access": "Not law: landowner",
              "local": "Cambridge ordinance"}


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
    """Setback sources for MGL c.131 §58: buildings where someone lives or sleeps."""
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


def building_footprints(cfg: dict, d: dict) -> gpd.GeoDataFrame:
    """Setback sources for MGL c.269 §12E: any dwelling or other building in use."""
    r = cfg["rules"]["buildings"]
    camb = d["buildings"][d["buildings"]["TYPE"].isin(r["cambridge_building_types"])].assign(source="Cambridge")
    nb = d["neighbor_buildings"]
    nb = nb[nb.geometry.area >= r["neighbor_min_footprint_sqft"]].assign(source="MassGIS (neighbor town)")
    out = pd.concat([camb[["source", "geometry"]], nb[["source", "geometry"]]], ignore_index=True)
    return gpd.GeoDataFrame(out, crs=cfg["project"]["crs"])


def static_exclusions(cfg: dict, d: dict) -> dict:
    """Exclusion geometries that don't change between scenarios."""
    city = d["city"]
    rules = cfg["rules"]

    rw = rules["water"]
    water = d["water"]
    water_geom = _union(water.geometry)
    if rw["reservoir_buffer_ft"] > 0:
        reservoirs = water[water["NAME"].isin(rw["reservoir_names"])]
        water_geom = _union([water_geom, _union(reservoirs.geometry).buffer(rw["reservoir_buffer_ft"])])

    roads = d["roads"]
    rh = rules["highways"]
    highways = _union(roads[roads["TYPE"].isin(rh["hard_surface_types"])].geometry).buffer(rh["setback_ft"])

    os_ = d["open_space"]
    owner = os_["OWNER"].fillna("").str.strip()
    parks = _union(os_[owner.isin([o.strip() for o in rules["parks"]["excluded_owners"]])].geometry)

    rl = rules["local"]
    streets = [_union(roads[roads["TYPE"].isin(rl["street_types"])].geometry)]
    if rl["include_bridges_layer"]:
        streets.append(_union(d["bridges"].geometry))

    return {k: _polys(g.intersection(city)) for k, g in
            {"water": water_geom, "highways": highways, "parks": parks, "streets_bridges": _union(streets)}.items()}


# ---------------------------------------------------------------- per scenario

def scenario_steps(cfg: dict, sc: dict, static: dict, setback: shapely.Geometry, city) -> list[dict]:
    """The waterfall for one scenario: one dict per layer, in STEPS order."""
    rules = cfg["rules"]
    src = rules[sc["setback_from"]]
    firearm = sc["weapon"] == "firearm"
    rl = rules["local"]
    meta = {
        "water": (rules["water"]["label"], rules["water"]["cite"], static["water"]),
        "setback": (f"{sc['setback_from']} + {sc['setback_ft']} ft", src["cite"], setback),
        "highways": (rules["highways"]["label"], rules["highways"]["cite"], static["highways"]),
        "parks": (rules["parks"]["label"], rules["parks"]["cite"], static["parks"]),
        "local": (rl["firearm_label"], rl["firearm_cite"], city) if firearm else
                 (rl["archery_label"], rl["archery_cite"], static["streets_bridges"]),
    }
    return [{"key": k, "tier": tier, "label": meta[k][0], "cite": meta[k][1], "geom": meta[k][2]} for k, tier in STEPS]


def waterfall(city, steps: list[dict]) -> tuple[list[dict], dict]:
    """Erase each step in order. Returns rows and what's left at the end of each tier."""
    rows = [{"tier": "", "step": "Cambridge city limits", "cite": "", "acres_remaining": _acres(city), "acres_removed": 0.0}]
    remaining, after = city, {}
    for s in steps:
        before = _acres(remaining)
        remaining = _polys(remaining.difference(s["geom"]))
        after[s["tier"]] = remaining
        rows.append({"tier": TIER_LABEL[s["tier"]], "step": s["label"], "cite": s["cite"],
                     "acres_remaining": _acres(remaining), "acres_removed": before - _acres(remaining)})

    # "Only this rule" = acres that would come back if this one rule alone were dropped.
    rows[0]["acres_only_this_rule"] = None
    for row, s in zip(rows[1:], steps):
        others = _union([o["geom"] for o in steps if o is not s])
        row["acres_only_this_rule"] = _acres(_polys(city.difference(others))) - _acres(remaining)
    return rows, after


def _candidates(geom, sid: str, min_sqft: float, crs) -> tuple[gpd.GeoDataFrame, int]:
    cand = gpd.GeoDataFrame(geometry=list(shapely.get_parts(geom)), crs=crs)
    n_raw = len(cand)
    cand = cand[cand.area >= min_sqft].reset_index(drop=True)
    cand["candidate_id"] = [f"{sid}-{i + 1:02d}" for i in range(len(cand))]
    cand["acres"] = cand.area / SQFT_PER_ACRE
    return cand, n_raw - len(cand)


def candidate_parcels(cfg: dict, candidates: gpd.GeoDataFrame, d: dict) -> gpd.GeoDataFrame:
    """Split candidates by land-use parcel so each piece names who owns it."""
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
    out_dir = cfg["project"]["out_dir"]
    min_sqft = cfg["project"]["min_candidate_sqft"]
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    gpkg.unlink(missing_ok=True)

    d = load_inputs(cfg)
    city = d["city"]

    def write(geom_or_gdf, layer: str) -> None:
        gdf = geom_or_gdf if isinstance(geom_or_gdf, gpd.GeoDataFrame) else \
            gpd.GeoDataFrame({"layer": [layer]}, geometry=[geom_or_gdf], crs=crs)
        gdf.to_file(gpkg, layer=layer, driver="GPKG")

    write(city, "city_limits")
    sources = {"dwellings": dwelling_footprints(cfg, d), "buildings": building_footprints(cfg, d)}
    write(sources["dwellings"], "dwelling_footprints")
    dw = sources["dwellings"]
    print(f"  dwellings: {(dw.source.str.startswith('Cambridge')).sum()} Cambridge + "
          f"{(~dw.source.str.startswith('Cambridge')).sum()} neighbor-town structures; "
          f"buildings in use (firearms): {len(sources['buildings'])}", flush=True)

    # Buffer each footprint, then union: same result as buffering the union (to arc
    # rounding) but ~30x faster than buffering one 277k-vertex multipolygon.
    setback_cache = {}

    def setback(src: str, ft: float):
        if (src, ft) not in setback_cache:
            setback_cache[src, ft] = _polys(_union(shapely.buffer(sources[src].geometry.values, ft)).intersection(city))
        return setback_cache[src, ft]

    print("  building shared exclusions (water, highways, parks, streets and bridges) ...", flush=True)
    static = static_exclusions(cfg, d)
    for k, g in static.items():
        write(g, f"excl_{k}")

    summary, tiers = [], []
    for sc in cfg["scenario"]:
        sid = sc["id"]
        print(f"  scenario {sid} ...", flush=True)
        sb = setback(sc["setback_from"], sc["setback_ft"])
        write(sb, f"excl_setback_{sid}")
        steps = scenario_steps(cfg, sc, static, sb, city)
        rows, after = waterfall(city, steps)

        # Three answers per scenario, slivers dropped from each:
        #   _law  dry land minus Massachusetts law (no parkland assumption, no Cambridge ordinance)
        #   _ma   plus the parkland assumption: what the statewide map would say for Cambridge
        #   final plus Cambridge's ordinances on top
        cand, n_slivers = _candidates(after["local"], sid, min_sqft, crs)
        ma_cand, _ = _candidates(after["access"], f"{sid}-ma", min_sqft, crs)
        law_cand, _ = _candidates(after["state"], f"{sid}-law", min_sqft, crs)
        rows.append({"tier": "", "step": f"slivers under {min_sqft:,} sq ft", "cite": "",
                     "acres_remaining": cand["acres"].sum(),
                     "acres_removed": rows[-1]["acres_remaining"] - cand["acres"].sum(),
                     "acres_only_this_rule": None})
        summary += [{"scenario": sid, **r} for r in rows]
        tiers.append({
            "scenario": sid, "label": sc["label"],
            "city_ac": _acres(city),
            "dry_land_ac": _acres(after["land"]),
            "state_law_ac": law_cand["acres"].sum(),
            "state_law_areas": len(law_cand),
            "plus_parkland_ac": ma_cand["acres"].sum(),
            "cambridge_ac": cand["acres"].sum(),
            "cambridge_areas": len(cand),
        })

        for tag, c in (("", cand), ("_law", law_cand)):
            if not len(c):
                continue
            write(c, f"candidates_{sid}{tag}")
            write(candidate_parcels(cfg, c, d), f"candidate_parcels_{sid}{tag}")
        print(f"    state law alone {law_cand['acres'].sum():.1f} ac; + parkland assumption "
              f"{ma_cand['acres'].sum():.1f} ac; + Cambridge's ordinances {cand['acres'].sum():.1f} ac "
              f"in {len(cand)} areas ({n_slivers} slivers dropped)", flush=True)

    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scenario", "tier", "step", "cite", "acres_remaining", "acres_removed",
                                          "acres_only_this_rule"])
        w.writeheader()
        for r in summary:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    with open(out_dir / "tiers.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(tiers[0].keys()))
        w.writeheader()
        for r in tiers:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    print(f"  -> {gpkg.name}, summary.csv, tiers.csv")
