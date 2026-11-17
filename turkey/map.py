"""Render the deliverables: a candidate map and a constraint-stack strip per scenario.

Colour roles (validated all-pairs, light surface): blue = land still in play,
orange = candidates / removed in this step. Everything else is neutral context.
"""

import csv

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import shapely  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from .analyze import RULE_ORDER  # noqa: E402
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

RULE_LABEL = {"dwellings": "dwellings", "parks": "public parkland", "roads": "streets + 150 ft", "water": "water + reservoir"}

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


def _site_table(parcels: gpd.GeoDataFrame, cand: gpd.GeoDataFrame) -> list[dict]:
    """One row per candidate polygon, described by its largest parcel piece."""
    rows = []
    for _, c in cand.sort_values("acres", ascending=False).iterrows():
        pieces = parcels[parcels["candidate_id"] == c["candidate_id"]].sort_values("acres", ascending=False)
        top = pieces.iloc[0] if len(pieces) else None
        name = "—"
        if top is not None:
            name = top["open_space_name"] if isinstance(top["open_space_name"], str) else top["location"]
        owners = sorted(set(pieces["owner_class"])) if len(pieces) else []
        rows.append({
            "candidate_id": c["candidate_id"],
            "site": name,
            "use": top["use_category"] if top is not None else "",
            "owner_class": owners[0] if len(owners) == 1 else f"{len(owners)} owner classes",
            "parcels": pieces["ml"].nunique(),
            "acres": c["acres"],
            "geometry": c.geometry,
        })
    return rows


def candidate_map(cfg, sc, layers, city, out_png, out_csv):
    gpkg = cfg["project"]["gpkg"]
    sid = sc["id"]
    cand = gpd.read_file(gpkg, layer=f"candidates_{sid}")
    parcels = gpd.read_file(gpkg, layer=f"candidate_parcels_{sid}")
    sites = _site_table(parcels, cand)

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
    fig.text(0.02, 0.945, f"Where an archer could stand in Cambridge: {total:.0f} acres", fontsize=17, fontweight="bold")
    fig.text(0.02, 0.905, f"{sc['label']} · {len(sites)} areas ≥ {cfg['project']['min_candidate_sqft']:,} sq ft "
             f"left after dwellings, parkland, streets and water are erased ({total / layers['city_acres']:.1%} of the city)",
             fontsize=10.5, color=INK_2)

    side.text(0, 1.0, "Largest candidate areas", fontsize=11, fontweight="bold", va="top")
    side.text(0, 0.955, "Every one still needs the landowner's yes.", fontsize=9, color=INK_2, va="top")
    y = 0.89
    for i, s in enumerate(sites[:n_label], 1):
        side.text(0.0, y, f"{i}", fontsize=9.5, fontweight="bold", va="top")
        side.text(0.06, y, s["site"][:34], fontsize=9.5, va="top")
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
    """Small multiples: the city after each rule is erased (slide 2, drawn)."""
    sid = sc["id"]
    labels = {**RULE_LABEL, "dwellings": f"dwellings + {sc['dwelling_setback_ft']} ft"}
    steps = [("Cambridge city limits", None)] + [(labels[r], r) for r in RULE_ORDER]
    excl = {"dwellings": layers[f"dwellings_{sid}"], **{k: layers[k] for k in ("parks", "roads", "water")}}
    acres = {r["step"]: float(r["acres_remaining"]) for r in summary if r["scenario"] == sid}

    fig, axes = plt.subplots(1, len(steps), figsize=(13.333, 3.9), dpi=150)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.76, bottom=0.11, wspace=0.04)
    removed = shapely.Polygon()
    remaining = city
    for ax, (label, rule) in zip(axes, steps):
        this = shapely.Polygon()
        if rule:
            this = remaining.intersection(excl[rule])
            remaining = remaining.difference(excl[rule])
        _plot_geom(ax, removed, color=REMOVED_EARLIER, linewidth=0)
        _plot_geom(ax, this, color=ORANGE, linewidth=0)
        _plot_geom(ax, remaining, color=BLUE, linewidth=0)
        _plot_geom(ax, city.boundary, color=INK_3, linewidth=0.5)
        _frame(ax, city)
        key = "Cambridge city limits" if rule is None else rule
        ax.set_title(("" if rule is None else "− ") + label, fontsize=10, color=INK_2, loc="left", pad=18)
        ax.text(0, 1.02, f"{acres[key]:,.0f} ac left", transform=ax.transAxes, fontsize=12, fontweight="bold")
        removed = shapely.union_all([removed, this])

    fig.text(0.01, 0.92, f"Each rule is one more layer · {sc['label']}", fontsize=15, fontweight="bold")
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
    layers = {
        "city_acres": city.area / SQFT_PER_ACRE,
        "buildings": gpd.read_file(raw / "buildings.geojson").to_crs(crs),
        "water": gpd.read_file(gpkg, layer="excl_water").union_all(),
        "parks": gpd.read_file(gpkg, layer="excl_parks").union_all(),
        "roads": gpd.read_file(gpkg, layer="excl_roads").union_all(),
    }
    with open(out / "summary.csv") as f:
        summary = list(csv.DictReader(f))

    for sc in cfg["scenario"]:
        if sc.get("citywide_ban"):
            continue  # 0 acres; slide 4 already makes that point
        sid = sc["id"]
        layers[f"dwellings_{sid}"] = gpd.read_file(gpkg, layer=f"excl_dwellings_{sid}").union_all()
        candidate_map(cfg, sc, layers, city, out / f"map_{sid}.png", out / f"candidates_{sid}.csv")
        stack_strip(cfg, sc, layers, city, summary, out / f"stack_{sid}.png")
        print(f"  -> map_{sid}.png, stack_{sid}.png, candidates_{sid}.csv")
