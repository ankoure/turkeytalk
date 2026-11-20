"""Render the deliverables: a candidate map and a constraint-stack strip per scenario.

Colour roles (validated all-pairs, light surface): blue = land still in play,
orange = candidates / removed in this step. Everything else is neutral context.
"""

import csv

import geopandas as gpd
import matplotlib
import pyogrio

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import shapely  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from .analyze import STEPS  # noqa: E402
from .config import SQFT_PER_ACRE  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8983"
CITY = "#f0efec"
BUILDING = "#d9d8d2"
WATER = "#cde2fb"
REMOVED_EARLIER = "#e4e3de"
BLUE = "#2a78d6"
ORANGE = "#eb6834"

TIER_SHORT = {"land": "DRY LAND", "state": "MASSACHUSETTS LAW", "access": "NOT LAW: LANDOWNER", "local": "CAMBRIDGE, ON TOP"}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "text.color": INK,
    "axes.edgecolor": INK_3,
    "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
})


def _plot_geom(ax, geom, **kw):
    if geom is None or geom.is_empty:
        return
    gpd.GeoSeries([geom]).plot(ax=ax, **kw)


def _frame(ax, city):
    x0, y0, x1, y1 = city.bounds
    pad = 600
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y0 - pad, y1 + pad)
    ax.set_aspect("equal")
    ax.set_axis_off()


def _scalebar(ax, city, miles=0.5):
    x0, y0, x1, _ = city.bounds
    ft = miles * 5280
    x, y = x1 - ft - 400, y0 + 300
    ax.plot([x, x + ft], [y, y], color=INK_2, lw=2, solid_capstyle="butt")
    ax.text(x + ft / 2, y + 250, f"{miles:g} mi", ha="center", va="bottom", fontsize=8, color=INK_2)
    ax.annotate("N", xy=(x1 - 200, y0 + 2600), xytext=(x1 - 200, y0 + 1500), ha="center", fontsize=9,
                color=INK_2, arrowprops={"arrowstyle": "-|>", "color": INK_2, "lw": 1.2})


OPEN_SPACE_OWNER = {"State": "Commonwealth", "City": "City of Cambridge", "Cambridge": "City of Cambridge", "Federal": "Federal"}


def _site_table(parcels: gpd.GeoDataFrame, cand: gpd.GeoDataFrame, open_space: gpd.GeoDataFrame) -> list[dict]:
    """One row per candidate polygon, described by its largest parcel piece (or, off the
    parcel map, e.g. on a riverbank, by the open space it falls in)."""
    pts = gpd.GeoDataFrame(cand[["candidate_id"]], geometry=cand.representative_point(), crs=cand.crs)
    os_hit = gpd.sjoin(pts, open_space[["NAME", "OWNER", "geometry"]], predicate="within", how="left")
    os_hit = os_hit.groupby("candidate_id")[["NAME", "OWNER"]].first()
    rows = []
    for _, c in cand.sort_values("acres", ascending=False).iterrows():
        pieces = parcels[parcels["candidate_id"] == c["candidate_id"]].sort_values("acres", ascending=False)
        top = pieces.iloc[0] if len(pieces) else None
        owners = sorted(set(pieces["owner_class"])) if len(pieces) else []
        if top is not None:
            name = top["open_space_name"] if isinstance(top["open_space_name"], str) else top["location"]
            use = top["use_category"]
            owner = owners[0] if len(owners) == 1 else f"{len(owners)} owner classes"
        else:
            o = os_hit.loc[c["candidate_id"]] if c["candidate_id"] in os_hit.index else None
            name = o["NAME"] if o is not None and isinstance(o["NAME"], str) else "—"
            use = "Open space, no tax parcel"
            owner = OPEN_SPACE_OWNER.get(o["OWNER"], "unknown owner") if o is not None else "unknown owner"
        rows.append({
            "candidate_id": c["candidate_id"],
            "site": name,
            "use": use,
            "owner_class": owner,
            "parcels": pieces["ml"].nunique(),
            "acres": c["acres"],
            "geometry": c.geometry,
        })
    return rows


def candidate_map(cfg, sc, layers, city, out_png, out_csv, tag="", title=None, subtitle=None):
    gpkg = cfg["project"]["gpkg"]
    sid = sc["id"]
    cand = gpd.read_file(gpkg, layer=f"candidates_{sid}{tag}")
    parcels = gpd.read_file(gpkg, layer=f"candidate_parcels_{sid}{tag}")
    sites = _site_table(parcels, cand, layers["open_space"])

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "candidate_id", "site", "use", "owner_class", "parcels", "acres"])
        w.writeheader()
        for i, s in enumerate(sites, 1):
            w.writerow({"rank": i, **{k: (round(v, 2) if k == "acres" else v) for k, v in s.items() if k != "geometry"}})

    fig = plt.figure(figsize=(13.333, 7.5), dpi=150)
    ax = fig.add_axes([0.02, 0.06, 0.62, 0.80])
    side = fig.add_axes([0.66, 0.06, 0.32, 0.80])
    side.set_axis_off()

    _plot_geom(ax, city, color=CITY, edgecolor=INK_3, linewidth=0.8, zorder=1)
    layers["buildings"].plot(ax=ax, color=BUILDING, linewidth=0, zorder=2)
    _plot_geom(ax, layers["water"], color=WATER, linewidth=0, zorder=3)
    cand.plot(ax=ax, color=ORANGE, edgecolor=SURFACE, linewidth=0.8, zorder=4)

    n_label = min(10, len(sites))
    for i, s in enumerate(sites[:n_label], 1):
        p = s["geometry"].representative_point()
        ax.annotate(str(i), (p.x, p.y), xytext=(6, 6), textcoords="offset points", fontsize=8.5,
                    fontweight="bold", color=INK, zorder=6,
                    bbox={"boxstyle": "circle,pad=0.18", "fc": SURFACE, "ec": ORANGE, "lw": 1})
    _frame(ax, city)
    _scalebar(ax, city)

    total = sum(s["acres"] for s in sites)
    fig.text(0.02, 0.945, (title or "Where an archer could stand in Cambridge: {acres} acres").format(acres=f"{total:.0f}"
             if total >= 1 else f"{total:.1f}"), fontsize=17, fontweight="bold")
    fig.text(0.02, 0.905, f"{sc['label']} · {len(sites)} area{'s' if len(sites) != 1 else ''} ≥ "
             f"{cfg['project']['min_candidate_sqft']:,} sq ft ({total / layers['city_acres']:.1%} of the city) · "
             + (subtitle or "after open water, state setbacks, public parkland and Cambridge's ordinances"),
             fontsize=10.5, color=INK_2)

    side.text(0, 1.0, "Largest candidate areas", fontsize=11, fontweight="bold", va="top")
    side.text(0, 0.955, "Every one is somebody's land. Ask first.", fontsize=9, color=INK_2, va="top")
    y = 0.89
    for i, s in enumerate(sites[:n_label], 1):
        side.text(0.0, y, f"{i}", fontsize=9.5, fontweight="bold", va="top")
        side.text(0.06, y, s["site"] if len(s["site"]) <= 38 else s["site"][:37] + "…", fontsize=9.5, va="top")
        side.text(1.0, y, f"{s['acres']:.1f} ac", fontsize=9.5, va="top", ha="right")
        side.text(0.06, y - 0.033, f"{s['use']} · {s['owner_class']}", fontsize=8, color=INK_2, va="top")
        y -= 0.082
    rest = sites[n_label:]
    if rest:
        side.text(0.06, y, f"+ {len(rest)} smaller area{'s' if len(rest) > 1 else ''}, "
                  f"{sum(s['acres'] for s in rest):.1f} ac",
                  fontsize=8.5, color=INK_2, va="top")

    side.legend(handles=[Patch(color=ORANGE, label="Candidate area"), Patch(color=WATER, label="Water"),
                         Patch(color=BUILDING, label="Building")],
                loc="lower left", frameon=False, fontsize=8.5, ncol=3, handlelength=1.2, bbox_to_anchor=(0, -0.02))

    commit = cfg["sources"]["cambridge"]["commit"][:7]
    fig.text(0.02, 0.02, f"Data: City of Cambridge GIS (cambridgegis_data@{commit}); MassGIS Building Structures. "
             f"EPSG:2249. Spatially possible ≠ legally permitted — a map is not a permission slip.",
             fontsize=7.5, color=INK_3)
    fig.savefig(out_png)
    plt.close(fig)


def stack_strip(cfg, sc, layers, city, summary, out_png):
    """Small multiples: the city after each rule is erased (slide 2, drawn), tier by tier."""
    sid = sc["id"]
    rows = [r for r in summary if r["scenario"] == sid]          # city limits, one per STEPS entry, slivers
    excl = {"setback": layers[f"setback_{sid}"], "local": city if sc["weapon"] == "firearm" else layers["streets_bridges"],
            **{k: layers[k] for k in ("water", "highways", "parks")}}
    panels = [(None, None, rows[0])] + [(key, tier, row) for (key, tier), row in zip(STEPS, rows[1:])]

    fig, axes = plt.subplots(1, len(panels), figsize=(13.333, 3.9), dpi=150)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.72, bottom=0.10, wspace=0.05)
    removed = shapely.Polygon()
    remaining = city
    for ax, (key, tier, row) in zip(axes, panels):
        this = shapely.Polygon()
        if key:
            this = remaining.intersection(excl[key])
            remaining = remaining.difference(excl[key])
        _plot_geom(ax, removed, color=REMOVED_EARLIER, linewidth=0)
        _plot_geom(ax, this, color=ORANGE, linewidth=0)
        _plot_geom(ax, remaining, color=BLUE, linewidth=0)
        _plot_geom(ax, city.boundary, color=INK_3, linewidth=0.5)
        _frame(ax, city)
        left = float(row["acres_remaining"])
        ax.text(0, 1.30, TIER_SHORT[tier] if tier else "START", transform=ax.transAxes, fontsize=7, color=INK_3,
                fontweight="bold")
        ax.text(0, 1.20, ("− " if key else "") + row["step"], transform=ax.transAxes, fontsize=9, color=INK)
        ax.text(0, 1.12, row["cite"] if key else "", transform=ax.transAxes, fontsize=7.5, color=INK_2)
        ax.text(0, 1.01, f"{left:,.0f} ac left" if left >= 1 or left == 0 else f"{left:.1f} ac left",
                transform=ax.transAxes, fontsize=12, fontweight="bold")
        removed = shapely.union_all([removed, this])

    fig.text(0.01, 0.93, f"Each rule is one more layer · {sc['label']}", fontsize=15, fontweight="bold")
    fig.legend(handles=[Patch(color=BLUE, label="Still in play"), Patch(color=ORANGE, label="Removed by this rule"),
                        Patch(color=REMOVED_EARLIER, label="Removed earlier")],
               loc="lower left", bbox_to_anchor=(0.01, 0.0), ncol=3, frameon=False, fontsize=9)
    fig.savefig(out_png)
    plt.close(fig)


def run(cfg: dict) -> None:
    print("map")
    gpkg = cfg["project"]["gpkg"]
    out = cfg["project"]["out_dir"]
    city = gpd.read_file(gpkg, layer="city_limits").union_all()
    raw = cfg["project"]["raw_dir"]
    crs = cfg["project"]["crs"]
    have = {name for name, _ in pyogrio.list_layers(gpkg)}
    layers = {
        "city_acres": city.area / SQFT_PER_ACRE,
        "buildings": gpd.read_file(raw / "buildings.geojson").to_crs(crs),
        "open_space": gpd.read_file(raw / "open_space.geojson").to_crs(crs),
        **{k: gpd.read_file(gpkg, layer=f"excl_{k}").union_all() for k in ("water", "highways", "parks", "streets_bridges")},
    }
    with open(out / "summary.csv") as f:
        summary = list(csv.DictReader(f))

    for sc in cfg["scenario"]:
        sid = sc["id"]
        layers[f"setback_{sid}"] = gpd.read_file(gpkg, layer=f"excl_setback_{sid}").union_all()
        stack_strip(cfg, sc, layers, city, summary, out / f"stack_{sid}.png")
        made = [f"stack_{sid}.png"]
        if f"candidates_{sid}" in have:
            candidate_map(cfg, sc, layers, city, out / f"map_{sid}.png", out / f"candidates_{sid}.csv")
            made += [f"map_{sid}.png", f"candidates_{sid}.csv"]
        if sc["weapon"] == "firearm" and f"candidates_{sid}_law" in have:
            # The firearm answer is 0; this map shows what Massachusetts law alone would leave.
            candidate_map(cfg, sc, layers, city, out / f"map_{sid}_law.png", out / f"candidates_{sid}_law.csv",
                          tag="_law", title="Where state law alone would allow a shotgun in Cambridge: {acres} acres",
                          subtitle="after open water and state setbacks only, before parkland and Cambridge's firearm ban")
            made += [f"map_{sid}_law.png", f"candidates_{sid}_law.csv"]
        print(f"  -> {', '.join(made)}")
