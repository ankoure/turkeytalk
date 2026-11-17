# turkeytalk

The spatial analysis behind the Fall NEARC 2026 lightning talk *"Can I hunt my Thanksgiving turkey in Cambridge?"*. It turns hunting rules into GIS layers, erases them from the city, and reports what's left.

| Scenario | Candidate land |
|---|---|
| Firearm | 0 acres (citywide discharge ban) |
| Archery, 500 ft dwelling setback (current) | 20.0 acres in 11 areas |
| Archery, 250 ft dwelling setback (April 2026 bill) | 78.1 acres in 32 areas |

How the answer was reached, what it means and its known gaps are in **[docs/methodology.md](docs/methodology.md)**. A candidate area is spatially possible, not legally permitted.

## Run it

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/). GDAL comes bundled with the `pyogrio` wheel, so nothing else is needed.

```bash
uv sync
uv run python -m turkey all
```

The first run downloads about 41 MB of City of Cambridge layers and queries MassGIS for buildings across the city line (about 40 s). Later runs reuse `data/raw/` and take about 20 s.

Each step can also be run on its own:

| Command | What it does |
|---|---|
| `uv run python -m turkey fetch` | Download raw layers into `data/raw/` and write `manifest.json` (source URL, SHA-256, size, download time). Add `--force` to re-download. |
| `uv run python -m turkey analyze` | Build the exclusion layers, run each scenario's waterfall, and write the GeoPackage and `summary.csv`. |
| `uv run python -m turkey map` | Render the maps and candidate tables from the GeoPackage. |

Without uv: `pip install geopandas pyogrio shapely matplotlib`, then `python -m turkey all`.

## Outputs

All outputs are written to `outputs/`.

| File | Contents |
|---|---|
| `turkeytalk.gpkg` | Every layer, in EPSG:2249 (opens in QGIS or ArcGIS Pro; see below) |
| `summary.csv` | Waterfall per scenario: acres left, removed by each step, and "only this rule" (acres that would come back if that rule alone were dropped) |
| `map_<scenario>.png` | Candidate map with the largest sites listed |
| `stack_<scenario>.png` | Small multiples: the city after each rule is erased |
| `candidates_<scenario>.csv` | One row per candidate area: site, use, owner class, number of parcels, acres |

The firearm scenario has no map; it's 0 acres.

GeoPackage layers:

| Layer | Contents |
|---|---|
| `city_limits` | Study area |
| `dwelling_footprints` | Buildings treated as dwellings, with a `source` column (Cambridge or neighbor town) |
| `excl_dwellings_<scenario>` | Dwelling setback for that scenario |
| `excl_parks`, `excl_roads`, `excl_water` | Exclusions shared by every scenario |
| `candidates_<scenario>` | What survives, one polygon per area |
| `candidate_parcels_<scenario>` | Candidates split by parcel, with map-lot (`ml`), address, use, owner class and open-space name |

## Changing the rules

Every regulatory number is in [`config.toml`](config.toml); the code holds none. Edit the file, then rerun `analyze` and `map`.

- **Change a setback:** edit `dwelling_setback_ft` in a `[[scenario]]` block, or `hard_surface_setback_ft` / `reservoir_buffer_ft` under `[rules.*]`.
- **Add a scenario:** add another `[[scenario]]` block with a new `id`. Its maps and GeoPackage layers are named after the `id`.
- **Change what counts as a dwelling, park or street:** edit the type, owner and use-category lists under `[rules.dwellings]`, `[rules.parks]` and `[rules.roads]`.

If a setback goes above 600 ft, raise `[sources.massgis_buildings] ring_ft` to match and run `fetch --force`. Otherwise buildings in neighboring towns stop short of the new buffer.

Adding a new kind of rule (a rail setback, for example) takes code: an exclusion builder and a `RULE_ORDER` entry in `turkey/analyze.py`, plus a label and stack-strip entry in `turkey/map.py`.

## Data

- **City of Cambridge GIS** ([cambridgegis/cambridgegis_data](https://github.com/cambridgegis/cambridgegis_data)), pinned to commit `f5d7cc6` (2026-11-03) so reruns give the same answer. To move to newer city data, change `commit` in `config.toml` and run `fetch --force`.
- **MassGIS Building Structures (2-D)**: structures outside Cambridge within 600 ft of the city line, queried live in tiles. This source isn't pinned, so a later `fetch --force` may pull edits MassGIS has made since.

`data/raw/` and the GeoPackage are gitignored; the PNGs and CSVs are small enough to commit.

## Layout

```
config.toml          rules, scenarios, sources
turkey/
  fetch.py           downloads + manifest
  analyze.py         exclusions, waterfall, candidates, owner tagging
  map.py             candidate maps, constraint-stack strips, site tables
docs/methodology.md  methodology, results, limitations
outputs/             generated
data/raw/            downloaded (gitignored)
```
