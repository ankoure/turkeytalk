# turkeytalk

The spatial analysis behind the Fall NEARC 2026 lightning talk *"Can I hunt my Thanksgiving turkey in Cambridge?"*. It turns hunting rules into GIS layers, erases them from the city, and reports what's left.

Layers are erased in tiers: open water, then Massachusetts law, then a landowner assumption that isn't law (public parkland), then Cambridge's own ordinances on top.

| Scenario | Massachusetts law alone | + parkland | + Cambridge ordinances |
|---|---:|---:|---:|
| Firearm, 500 ft from any building in use | 39.2 ac | 0 | **0** |
| Archery, 500 ft from dwellings (current law) | 132.4 ac | 20.0 ac | **20.0 ac in 11 areas** |
| Archery, 250 ft from dwellings (pending bill) | 299.3 ac | 78.2 ac | **78.2 ac in 32 areas** |

How the answer was reached, what it means and its known gaps are in **[docs/methodology.md](docs/methodology.md)**. Every rule the talk states is checked against its source in **[docs/claims.md](docs/claims.md)**. A candidate area is spatially possible, not legally permitted.

The same analysis also runs for **all 351 Massachusetts cities and towns** on MassGIS data, behind an interactive map (`web/`). See [Statewide](#statewide) below and **[docs/statewide.md](docs/statewide.md)**.

## Run it

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/). GDAL comes bundled with the `pyogrio` wheel, so nothing else is needed.

```bash
uv sync
uv run python -m turkey all
```

The first run downloads about 41 MB of City of Cambridge layers and queries MassGIS for buildings across the city line (about 40 s). Later runs reuse `data/raw/` and take about 30 s.

Each step can also be run on its own:

| Command | What it does |
|---|---|
| `uv run python -m turkey fetch` | Download raw layers into `data/raw/` and write `manifest.json` (source URL, SHA-256, size, download time). Add `--force` to re-download. |
| `uv run python -m turkey analyze` | Build the exclusion layers, run each scenario's waterfall, and write the GeoPackage, `summary.csv` and `tiers.csv`. |
| `uv run python -m turkey map` | Render the maps and candidate tables from the GeoPackage. |

Without uv: `pip install geopandas pyogrio shapely matplotlib`, then `python -m turkey all`.

## Outputs

All outputs are written to `outputs/`.

| File | Contents |
|---|---|
| `turkeytalk.gpkg` | Every layer, in EPSG:2249 (opens in QGIS or ArcGIS Pro; see below) |
| `summary.csv` | Waterfall per scenario: tier, citation, acres left, removed by each step, and "only this rule" (acres that would come back if that rule alone were dropped) |
| `tiers.csv` | One row per scenario: acres after Massachusetts law alone, after the parkland assumption, and after Cambridge's ordinances |
| `stack_<scenario>.png` | Small multiples: the city after each rule is erased, tier by tier |
| `map_<scenario>.png` | Candidate map with the largest sites listed (archery scenarios) |
| `map_firearm_law.png` | What Massachusetts law alone would leave for a firearm (the final answer is 0) |
| `candidates_<scenario>.csv` | One row per candidate area: site, use, owner class, number of parcels, acres |

GeoPackage layers:

| Layer | Contents |
|---|---|
| `city_limits` | Study area |
| `dwelling_footprints` | Buildings treated as dwellings, with a `source` column (Cambridge or neighbor town) |
| `excl_setback_<scenario>` | That scenario's setback: dwellings (MGL c.131 §58) or any building in use (c.269 §12E) |
| `excl_water`, `excl_highways`, `excl_parks`, `excl_streets_bridges` | Exclusions shared by every scenario |
| `candidates_<scenario>` | What survives every tier, one polygon per area |
| `candidates_<scenario>_law` | What survives Massachusetts law alone (open water and state setbacks only) |
| `candidate_parcels_<scenario>[_law]` | Candidates split by parcel, with map-lot (`ml`), address, use, owner class and open-space name |

## Changing the rules

Every regulatory number is in [`config.toml`](config.toml); the code holds none. Edit the file, then rerun `analyze` and `map`.

- **Change a setback:** edit `setback_ft` in a `[[scenario]]` block, or `setback_ft` under `[rules.highways]`. `reservoir_buffer_ft` under `[rules.water]` is 0 because no hunting rule backs it; raise it to model one.
- **Add a scenario:** add another `[[scenario]]` block with a new `id`, a `weapon` (`archery` or `firearm`, which picks Cambridge's rule) and a `setback_from` (`dwellings` or `buildings`, which picks the state rule). Its maps and GeoPackage layers are named after the `id`.
- **Change what counts as a dwelling, building, park or street:** edit the type, owner and use-category lists under `[rules.dwellings]`, `[rules.buildings]`, `[rules.parks]`, `[rules.highways]` and `[rules.local]`.

If a setback goes above 600 ft, raise `[sources.massgis_buildings] ring_ft` to match and run `fetch --force`. Otherwise buildings in neighboring towns stop short of the new buffer.

Adding a new kind of rule (a rail setback, for example) takes code: an exclusion builder in `static_exclusions`, a `STEPS` entry with its tier, and a label in `scenario_steps`, all in `turkey/analyze.py`, plus an entry in `stack_strip` in `turkey/map.py`.

## Data

- **City of Cambridge GIS** ([cambridgegis/cambridgegis_data](https://github.com/cambridgegis/cambridgegis_data)), pinned to commit `f5d7cc6` (2026-11-03) so reruns give the same answer. To move to newer city data, change `commit` in `config.toml` and run `fetch --force`.
- **MassGIS Building Structures (2-D)**: structures outside Cambridge within 600 ft of the city line, queried live in tiles. This source isn't pinned, so a later `fetch --force` may pull edits MassGIS has made since.

`data/raw/` and the GeoPackage are gitignored; the PNGs and CSVs are small enough to commit.

## Statewide

The statewide pipeline swaps every source for MassGIS: building structures, L3 parcels, protected open space, MassDOT roads and 1:25k hydrography. Every town gets Massachusetts law plus the same two labeled assumptions (open water, public recreation land); town ordinances go on top, and only Cambridge's are in so far. Its rules and parameters are in [`massachusetts.toml`](massachusetts.toml), and how they differ from the Cambridge version is explained in [docs/statewide.md](docs/statewide.md).

```bash
uv run python -m turkey ma all
```

| Step | What it does | Time |
|---|---|---|
| `ma fetch` | Download statewide layers and 351 per-town parcel archives from MassGIS (about 1.6 GB of zips, 6.8 GB unpacked) into `data/raw/massgis/` | Several minutes |
| `ma prep` | Build `data/massgis/prepared.gpkg`: dwelling flags, owner categories, road and park flags, all spatially indexed (1.7 GB) | A few minutes |
| `ma run` | Run every town in parallel. `--towns Petersham,Concord` runs a subset; `--workers N` sets parallelism | About 2 minutes for all 351 on 12 workers |
| `ma web` | Build `web/index.html` and `web/data/` (52 MB) | Seconds |

Rerunning `ma prep` builds only the layers that are missing; `--force` rebuilds everything.

Outputs in `data/massgis/out/`:

| File | Contents |
|---|---|
| `summary.csv` | One row per town: acres per scenario under state law (`<scenario>_state_ac`) and with the town's own rules (`<scenario>_ac`), and by who decides (MassWildlife, other public, land trust or nonprofit, private) |
| `towns/NNN.gpkg` | Full-resolution candidate pieces per scenario, with category and open-space site name (NNN = MassGIS TOWN_ID) |
| `web/NNN.json` | Simplified per-town data the web build re-encodes |

### The interactive map

`web/index.html` is a static page: Leaflet from cdnjs plus the JSON in `web/data/`, with no tile server or backend. To try it locally:

```bash
cd web && python3 -m http.server 8000    # then open http://localhost:8000
```

It also runs as-is on any static host (GitHub Pages, S3). Town links use the hash, e.g. `#petersham`.

The template is `web/src/index.html`; `ma web` inlines Leaflet's stylesheet into `web/index.html`. Geometry in `web/data/` is delta-encoded integers rather than GeoJSON coordinates, which makes it about 3x smaller (format notes in `turkey/ma_web.py`).

### Adding a town's own rules

Municipal bylaws aren't in any statewide dataset; only Cambridge's are applied, on top of the state result. To add another town, give it a `[local.<TOWN_ID>]` block in `massachusetts.toml` with any of `firearm_ban = true` (firearm scenarios go to 0), `street_ban = true` (archery: every road surface is erased, paved or not) and a `note` (shown on the map), then rerun `ma run --towns <name>` and `ma web`. The town panel then shows both numbers: state law alone and with the town's rules.

## Layout

```
config.toml          Cambridge rules, scenarios, sources
massachusetts.toml   statewide rules, scenarios, sources, local overrides
turkey/
  fetch.py           Cambridge downloads + manifest
  analyze.py         Cambridge exclusions, waterfall, candidates, owner tagging
  map.py             Cambridge maps, constraint-stack strips, site tables
  ma_fetch.py        MassGIS downloads + manifest
  ma_prep.py         statewide prepared GeoPackage
  ma_analyze.py      per-town constraint stack (parallel)
  ma_web.py          web data + page build
web/
  src/index.html     map page (template)
  index.html         built page
  data/              built map data
docs/
  claims.md          every hunting rule the talk states, checked against its source
  methodology.md     Cambridge method, results, limitations
  statewide.md       statewide method, validation, limitations
outputs/             Cambridge outputs
data/raw/            downloads (gitignored)
data/massgis/        statewide working data and outputs (gitignored)
```
