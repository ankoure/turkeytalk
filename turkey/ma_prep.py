"""Turn raw MassGIS downloads into one analysis-ready GeoPackage.

Layers in data/massgis/prepared.gpkg (EPSG:26986, spatially indexed):
  towns       351 municipalities
  parcels     every L3 tax parcel: dwelling flag, owner category from use code
  structures  every MassGIS building footprint, tagged with its parcel's dwelling flag
  roads       MassDOT centerlines: hard-surface flag, half width (m)
  water       open water from 1:25k hydrography
  zone_a      MassDEP surface water supply Zone A
  openspace   protected/recreational open space: park flag, owner category, site name
"""

import glob
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from .config import FT

CAT_DFG, CAT_PUBLIC, CAT_CONS, CAT_PRIVATE = "dfg", "public", "conservation", "private"


def _write(gdf: gpd.GeoDataFrame, path: Path, layer: str) -> None:
    pyogrio.write_dataframe(gdf, path, layer=layer, driver="GPKG", spatial_index=True)
    print(f"  {layer}: {len(gdf):,}", flush=True)


def _polys(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = shapely.make_valid(gdf.geometry.values)
    gdf = gdf.explode(index_parts=False)
    gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon"])]
    return gdf


# ---------------------------------------------------------------- parcels

def _use3(code) -> str:
    m = re.match(r"\s*(\d{3})", str(code or ""))
    return m.group(1) if m else ""


def _is_dwelling_code(u: str, rules: dict) -> bool:
    if not u:
        return False
    if u[0] == "1":
        return u not in rules["residential_exclude"]
    if u[0] == "0":
        return "1" in u[1:]          # mixed use with a residential component
    return u in rules["overnight_codes"]


def _owner_cat(u: str, cats: dict) -> str:
    if u in cats["dfg_use_codes"]:
        return CAT_DFG
    if any(u.startswith(p) for p in cats["public_use_prefixes"]):
        return CAT_PUBLIC
    return CAT_PRIVATE


def _prep_town_parcels(args) -> gpd.GeoDataFrame | None:
    town_dir, rules, cats = args
    taxpar = glob.glob(f"{town_dir}/**/*TaxPar*.shp", recursive=True)
    assess = glob.glob(f"{town_dir}/**/*Assess*.dbf", recursive=True)
    if not taxpar:
        return None
    par = gpd.read_file(taxpar[0], columns=["LOC_ID", "POLY_TYPE", "TOWN_ID"], engine="pyogrio")
    par = _polys(par.to_crs(26986))
    if assess:
        a = pyogrio.read_dataframe(assess[0], columns=["LOC_ID", "USE_CODE", "UNITS"], read_geometry=False)
        a["u3"] = a["USE_CODE"].map(_use3)
        # UNITS only decides when the use code can't: some assessors (Cambridge among them)
        # fill UNITS on office and retail records with commercial units, not homes.
        units = pd.to_numeric(a["UNITS"], errors="coerce").fillna(0) > 0
        undecided = (a["u3"] == "") | a["u3"].str.startswith("0")
        a["dw"] = a["u3"].map(lambda u: _is_dwelling_code(u, rules)) | (units & undecided)
        a["cat"] = a["u3"].map(lambda u: _owner_cat(u, cats))
        g = a.groupby("LOC_ID")
        agg = pd.DataFrame({
            "dwelling": g["dw"].any(),
            "has_use": g["u3"].agg(lambda s: (s != "").any()),
            "use3": g["u3"].agg(lambda s: s[s != ""].mode().iat[0] if (s != "").any() else ""),
            # A parcel is public/DFG only if every assessing record on it says so (condos stay private).
            "owner_cat": g["cat"].agg(lambda s: s.iat[0] if s.nunique() == 1 else CAT_PRIVATE),
        })
        par = par.merge(agg, left_on="LOC_ID", right_index=True, how="left")
    # Parcels with no assessing record come out of the merge as NaN. Cast explicitly:
    # an object column of True/False/NaN would be written to the GeoPackage as text.
    for col in ("dwelling", "has_use"):
        par[col] = par[col].astype("boolean").fillna(False).astype(bool) if col in par else False
    for col, default in (("use3", ""), ("owner_cat", CAT_PRIVATE)):
        par[col] = par[col].fillna(default).astype(str) if col in par else default
    # Right-of-way and water polygons are not anyone's yard.
    par["row"] = par["POLY_TYPE"].fillna("").isin(["ROW", "PRIV_ROW", "RAIL_ROW", "WATER"])
    return par[["LOC_ID", "TOWN_ID", "use3", "dwelling", "has_use", "owner_cat", "row", "geometry"]]


def prep_parcels(cfg: dict) -> gpd.GeoDataFrame:
    raw = Path(cfg["project"]["raw_dir"])
    dirs = sorted((raw / "parcels").glob("[0-9][0-9][0-9]"))
    jobs = [(str(d), cfg["rules"]["dwellings"], cfg["rules"]["categories"]) for d in dirs]
    with ProcessPoolExecutor(min(16, cfg["project"]["workers"] + 4)) as pool:
        parts = [p for p in pool.map(_prep_town_parcels, jobs) if p is not None]
    missing = len(dirs) - len(parts)
    if missing:
        print(f"  warning: {missing} towns have no parcel shapefile")
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=26986)


# ---------------------------------------------------------------- structures

def prep_structures(cfg: dict, parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    raw = Path(cfg["project"]["raw_dir"])
    gdb = next((raw / "state" / "structures").glob("**/*.gdb"))
    layer = next(name for name, gt in pyogrio.list_layers(gdb) if gt and "Polygon" in gt)
    # Some footprints are stored as curves (MultiSurface), which shapely can't read.
    # GDAL linearizes them once into a scratch GeoPackage next to the prepared one.
    lin = Path(cfg["project"]["work_gpkg"]).with_name("structures_linear.gpkg")
    if not lin.exists():
        subprocess.run(["ogr2ogr", "-f", "GPKG", str(lin), str(gdb), layer, "-nln", "structures",
                        "-select", "STRUCT_ID", "-nlt", "CONVERT_TO_LINEAR", "-t_srs", "EPSG:26986"], check=True)
    s = gpd.read_file(lin, layer="structures", engine="pyogrio", use_arrow=True)
    s = s[s.geometry.notna() & ~s.geometry.is_empty].reset_index(drop=True)
    s["area_sqft"] = s.geometry.area / FT ** 2

    pts = gpd.GeoDataFrame(geometry=s.geometry.representative_point(), crs=26986)
    land = parcels[~parcels["row"]][["dwelling", "has_use", "geometry"]]
    hit = gpd.sjoin(pts, land, predicate="within", how="left")
    # Unmatched buildings come back as NaN; any matching parcel that says "dwelling" wins.
    flags = pd.DataFrame({"dwelling": hit["dwelling"].astype(float).fillna(0) > 0,
                          "has_use": hit["has_use"].astype(float).fillna(0) > 0}).groupby(level=0).max()
    s["parcel_dwelling"] = flags["dwelling"].reindex(s.index, fill_value=False).to_numpy(bool)
    s["parcel_known"] = flags["has_use"].reindex(s.index, fill_value=False).to_numpy(bool)
    return s


# ---------------------------------------------------------------- the rest

def prep_roads(cfg: dict) -> gpd.GeoDataFrame:
    r = cfg["rules"]["roads"]
    path = next((Path(cfg["project"]["raw_dir"]) / "state" / "roads").glob("**/EOTROADS_ARC.shp"))
    roads = gpd.read_file(path, columns=["RDTYPE", "SURFACE_TP", "SURFACE_WD", "STREETNAME"], engine="pyogrio", use_arrow=True)
    roads = roads[~roads["RDTYPE"].isin([8, 9, 10, 11])]      # tunnels: no surface to stand on or shoot across
    tp = roads["SURFACE_TP"].fillna(0).astype(int)
    roads["hard"] = tp.isin(r["hard_surface_codes"]) | ((tp == 0) & r["unknown_surface_is_hard"])
    wd = pd.to_numeric(roads["SURFACE_WD"], errors="coerce")   # feet in the Road Inventory
    roads["half_w_m"] = np.where(wd > 0, wd / 2, r["default_half_width_ft"]) * FT
    roads["rdtype"] = roads["RDTYPE"].fillna(6).astype(int)
    return roads[["rdtype", "hard", "half_w_m", "STREETNAME", "geometry"]].to_crs(26986)


def prep_water(cfg: dict) -> gpd.GeoDataFrame:
    path = next((Path(cfg["project"]["raw_dir"]) / "state" / "hydro").glob("**/HYDRO25K_POLY.shp"))
    w = gpd.read_file(path, columns=["POLY_CODE", "NAME"], engine="pyogrio", use_arrow=True)
    w = w[w["POLY_CODE"].isin(cfg["rules"]["water"]["open_water_codes"])]
    return _polys(w.to_crs(26986))


def prep_zone_a(cfg: dict) -> gpd.GeoDataFrame:
    path = next((Path(cfg["project"]["raw_dir"]) / "state" / "zone_a").glob("**/SWP_ZONES_POLY.shp"))
    z = gpd.read_file(path, columns=["SWPZONE", "SITE_NAME", "SUPPLIER"], engine="pyogrio").to_crs(26986)
    z = z[z["SWPZONE"].isin(["A", "R"])]     # Zone A and the reservoir it surrounds
    return _polys(z)


def prep_openspace(cfg: dict) -> gpd.GeoDataFrame:
    path = next((Path(cfg["project"]["raw_dir"]) / "state" / "openspace").glob("**/OPENSPACE_POLY.shp"))
    cols = ["SITE_NAME", "FEE_OWNER", "OWNER_ABRV", "OWNER_TYPE", "MANAGER", "PRIM_PURP", "PUB_ACCESS"]
    o = gpd.read_file(path, columns=cols, engine="pyogrio", use_arrow=True).to_crs(26986)
    p, c = cfg["rules"]["parks"], cfg["rules"]["categories"]
    ot = o["OWNER_TYPE"].fillna("X")
    o["park"] = o["PRIM_PURP"].isin(p["purposes"]) & (
        ot.isin(p["public_owner_types"]) | (p["also_if_public_access"] & (o["PUB_ACCESS"] == "Y")))
    o["cat"] = np.select(
        [o["OWNER_ABRV"].isin(c["dfg_owner_abbrevs"]), ot.isin(c["public_owner_types"]), ot.isin(c["conservation_owner_types"])],
        [CAT_DFG, CAT_PUBLIC, CAT_CONS], default=CAT_PRIVATE)
    o = o.rename(columns={"SITE_NAME": "site", "FEE_OWNER": "owner"})
    return _polys(o[["site", "owner", "OWNER_TYPE", "PRIM_PURP", "PUB_ACCESS", "park", "cat", "geometry"]])


def prep_towns(cfg: dict) -> gpd.GeoDataFrame:
    path = next((Path(cfg["project"]["raw_dir"]) / "state" / "towns").glob("**/TOWNSSURVEY_POLYM.shp"))
    t = gpd.read_file(path, columns=["TOWN", "TOWN_ID", "COUNTY", "POP2020", "AREA_ACRES"], engine="pyogrio")
    t["TOWN"] = t["TOWN"].str.title()
    return t.to_crs(26986)


def run(cfg: dict, force: bool = False) -> None:
    """Build each missing layer; --force rebuilds everything."""
    out = Path(cfg["project"]["work_gpkg"])
    out.parent.mkdir(parents=True, exist_ok=True)
    if force:
        out.unlink(missing_ok=True)
        out.with_name("structures_linear.gpkg").unlink(missing_ok=True)
    have = {name for name, _ in pyogrio.list_layers(out)} if out.exists() else set()
    print("ma prep" + (f" (keeping {', '.join(sorted(have))})" if have else ""))
    for name, build in (("towns", prep_towns), ("water", prep_water), ("zone_a", prep_zone_a),
                        ("openspace", prep_openspace), ("roads", prep_roads), ("parcels", prep_parcels)):
        if name not in have:
            _write(build(cfg), out, name)
    if "structures" not in have:
        parcels = pyogrio.read_dataframe(out, layer="parcels", columns=["dwelling", "has_use", "row"], use_arrow=True)
        _write(prep_structures(cfg, parcels), out, "structures")
