# Statewide version: methodology

The Cambridge analysis ([methodology.md](methodology.md)) used City of Cambridge data. This version swaps every source for a statewide MassGIS layer, so the same constraint stack runs for all 351 cities and towns. It feeds the interactive map in `web/`.

Every town gets the same base: Massachusetts law plus two labeled assumptions. Town ordinances go on top, and only Cambridge's are in the data so far. Each rule's source is checked in [claims.md](claims.md).

## Summary

| Scenario | Candidate land | Share of state | Median town |
|---|---:|---:|---:|
| Archery, 500 ft from dwellings (current law) | 2,087,577 ac | 40.3% | 31% |
| Archery, 250 ft from dwellings (pending bill) | 2,728,142 ac | 52.7% | 45% |
| Firearm, 500 ft from any building in use | 1,905,222 ac | 36.8% | 26% |

At 500 ft for archery, the share of a town that survives runs from 0.2% (Arlington) to 84% (New Ashford). The dense inner ring is lowest: Arlington, Cambridge, Marblehead, Watertown and Somerville all come in under 1.3%. The Berkshire and Franklin County hill towns are highest, at 80% or more.

Firearms leave nothing in Somerville or Watertown under state law alone. In Cambridge, state law leaves 6.1 acres (in the Alewife Brook Reservation and on Fresh Pond's water-supply land), and the city's discharge ban takes it to 0.

Who decides, for the 500 ft archery acres: MassWildlife land 9%, other public land 33%, land trusts and nonprofits 6%, private or institutional 53%.

The firearm row describes the shotgun weeks only. Fall turkey season 2026 is archery-only on Thanksgiving (Thursday, November 26) in every zone.

A candidate acre is one where the rules as mapped here don't rule hunting out. It is not a place where hunting is permitted.

## What changed from the Cambridge version

| | Cambridge version | Statewide version |
|---|---|---|
| Buildings | City footprints (with a type field) + MassGIS across the line | MassGIS Building Structures everywhere |
| What's a dwelling | City land-use parcel: units, group quarters or overnight use | MassGIS L3 parcel use code (state DOR codes); see below |
| Roads | City road-surface polygons | MassDOT centerlines buffered by half their surface width |
| Parkland | All public open space | Public open space whose primary purpose is recreation or historic |
| Town ordinances | Cambridge's, from city road and bridge polygons | Cambridge's, from MassDOT roads; no other town yet |
| Units | US survey feet (EPSG:2249) | Meters (EPSG:26986); distances set in feet and converted |

The parkland difference is deliberate. Statewide, erasing all public open space would also erase the state forests and Wildlife Management Areas where most Massachusetts hunting happens. So only recreation and historic sites (ball fields, playgrounds, town commons, historic houses) are erased. Everything else stays in and is labeled by who manages it. That also matches the law: DCR land is open to hunting by default unless listed in 302 CMR 12.11.

## What changed after the claims review (November 2026)

| Change | Why | Effect at 500 ft |
|---|---|---|
| MassDEP Zone A no longer erased (`exclude_zone_a = false`) | Zone A is a land-use definition (310 CMR 22.02), not a hunting rule; DCR issues hunting permits on its watershed lands | +90,000 ac statewide, almost all in the Quabbin, Wachusett and Ware River watersheds (Hubbardston +6,300, Rutland +4,800, Princeton +4,300, New Salem +4,100) |
| Unpaved road surfaces no longer erased statewide (`exclude_unpaved_surface = false`) | MGL c.131 §58 covers hard-surfaced highways only | Small; dirt roads stay in play |
| Cambridge's street-and-bridge bow ban applied as a local rule (`street_ban = true`) | Cambridge §9.16.060 | 0 ac: §58's 150 ft buffer already covers every paved Cambridge street |
| State result and local result reported separately (`<scenario>_state_ac`) | Town rules go on top | Cambridge firearm: 6.1 ac → 0 |
| Web map: posting rule, not "written permission"; Thanksgiving season note | [claims.md](claims.md) #7, #4 | Text only |

No town lost acreage; 239 gained more than an acre.

## Rules as geometry

| Tier | Rule (paraphrased) | Source | Data | Operation | Parameter |
|---|---|---|---|---|---|
| Massachusetts law | Archery: no hunting within the setback of an occupied dwelling on someone else's land | MGL c.131 §58; pending bill | Structures + L3 parcels | Buffer + erase | 500 ft or 250 ft |
| Massachusetts law | Firearm: no discharge within 500 ft of a dwelling or other building in use | MGL c.269 §12E | Structures | Buffer + erase | 500 ft |
| Massachusetts law | No firearm or arrow upon, across or within 150 ft of a state or hard-surfaced highway | MGL c.131 §58 | MassDOT roads | Buffer + erase | 150 ft past the surface edge |
| Assumption (physical) | Off the water | — | 1:25k hydrography | Erase | Open water |
| Assumption (landowner) | Public recreation and historic land: the owner says no | None; see [claims.md](claims.md) #5 | Protected and Recreational Open Space | Erase | `PRIM_PURP` R or H, public owner or full public access |
| Tag only | Who has to say yes | MGL c.131 §36 (where posted) | Open space owner + parcel use code | Overlay + tag | Four categories |
| Town ordinance, on top | Cambridge: no firearm discharge; no bows in streets or on bridges | Cambridge §9.16.010, §9.16.060 (unverified) | Town boundary; MassDOT roads | Erase | `[local.49]` |
| Off by default | Not near a drinking-water supply | None (310 CMR 22.02 Zone A as a proxy) | SWP Zones | Erase | `exclude_zone_a` |

All parameters are in [`massachusetts.toml`](../massachusetts.toml).

## Data

| Layer | Source | Version | Used for |
|---|---|---|---|
| Building Structures (2-D) | MassGIS | 2026-05-20 | Setbacks (2,678,976 footprints) |
| L3 Property Tax Parcels | MassGIS | FY2018–FY2027 by town; 338 of 351 towns FY2025 or later | Dwelling flag, owner category (2,211,479 parcels) |
| Protected and Recreational Open Space | MassGIS | 2026-07-28 | Parkland, owner category, site names |
| MassDOT Roads | MassGIS / MassDOT Road Inventory | 2026-02-04 | Hard-surfaced highways; Cambridge's streets |
| Hydrography 1:25,000 | MassGIS / MassDEP | 2019-12 | Open water |
| Surface Water Supply Protection Zones | MassDEP | 2023-05 | Zone A (off by default) |
| Town boundaries (TOWNSSURVEY_POLYM) | MassGIS | 2024-05 | Study areas |

All of it is bulk-downloaded from MassGIS's S3 bucket. The per-town parcel links come from MassGIS's L3 download index. Hashes and URLs are in `data/raw/massgis/manifest.json`.

## Method

1. **Prepare once** (`ma prep`, a few minutes). Everything goes into one GeoPackage with spatial indexes:
   - each parcel gets a dwelling flag and an owner category from its assessing records;
   - each building inherits its parcel's flag, by a point inside its footprint;
   - each road gets a hard-surface flag and a half-width;
   - each open-space polygon gets a park flag and an owner category.
2. **Dwellings.** A parcel is a dwelling parcel if any assessing record on it has one of these use codes:
   - **Residential:** any 1xx except accessory land (106), vacant land (130–132) and child care (140).
   - **Mixed use** with a residential part: 0xx with a 1 in either later digit.
   - **Places people sleep:** hotels and inns, nursing homes, hospitals, care facilities, camps, state institutions, UMass and state colleges, private schools and colleges, charitable housing, rectories, housing authorities, county jails.

   A units count above zero also counts, but only when the use code is blank or mixed. Some assessors, Cambridge's among them, put commercial unit counts in that field on office and retail records.

   For archery, a building counts if it is at least 300 sq ft and sits on a dwelling parcel, or on no known parcel. For firearms, every building counts.
3. **Per town** (`ma run`, about 2 minutes for all 351 on 12 workers):
   - Read every layer within the largest setback of the town line, so buildings and roads across the border still count.
   - Buffer, union and erase the state-law and assumption layers, then drop pieces under 10,000 sq ft. This is the state result.
   - For towns with a `[local]` block, apply the town's rules on top and drop slivers again. This is the final result; elsewhere it equals the state result.
   - Buffers use 4 segments per quarter circle, with the distance enlarged by 1/cos(π/16) so the polygon contains the true circle. The approximation can only shrink candidates, by at most about 2%.
4. **Who decides.** Candidate land is split into four categories, checked in this order:
   1. MassWildlife: Department of Fish and Game land, from the open-space owner or use codes 911 and 921.
   2. Other public: federal, state, county or municipal owners, or exempt public use codes.
   3. Land trust or nonprofit.
   4. Private or institutional: everything else. State law requires the owner's permission only on posted land (MGL c.131 §36), though some towns require written permission.

   Pieces inside named open space keep the site name.
5. **Web** (`ma web`). Each town's candidates are simplified to 3 m and stored once, tagged with the strictest scenario they survive. The scenarios nest (firearm ⊆ archery 500 ft ⊆ archery 250 ft), local rules included. Geometry is delta-encoded as integers at 1e-5 degrees, which brings the whole state to 52 MB. Towns with local rules also carry the state-only acreage, shown in the town panel.
6. **Who to ask** (in the browser). Clicking candidate land queries MassGIS's statewide tax-parcel service at that point and shows the lot's owner of record and mailing address, with an option to draft a permission letter. The piece's category comes from an exact point-in-polygon test on the candidate data, not from Leaflet's hit test, which also counts clicks a few pixels outside a shape's edge.

## Validation against the Cambridge version

Cambridge is the one town with both a city-data and a MassGIS answer.

| Scenario | City data | MassGIS |
|---|---:|---:|
| Archery, 500 ft | 20.0 ac | 39.0 ac |
| Archery, 250 ft | 78.2 ac | 113.3 ac |
| Firearm | 0 | 0 |
| Firearm, before Cambridge's ban | 0 (39.2 ac before the parkland assumption) | 6.1 ac |

The two runs share 10.1 acres at 500 ft. The MassGIS run finds another 28.6 acres in pieces over 0.2 acre, and 26.5 of those lie inside the city run's parkland exclusion. Most of that is DCR's Alewife Brook Reservation and part of the Charles River Reservation. Statewide, those are "Recreation and Conservation" land and are kept as "other public land, check the managing agency" rather than erased. The dwelling setback accounts for only 1.6 of the 28.6 acres.

That dwelling agreement came only after the units fix described above. Before it, MassGIS found 4.0 acres in Cambridge, because about 600 office, retail and lab buildings counted as dwellings.

## Known gaps and limitations

- **DCR land can't be sorted from the data.** State forests where hunting is common (October Mountain, Myles Standish, Harold Parker) share an open-space purpose code with urban reservations. 302 CMR 12.11 opens DCR land to hunting by default but closes listed properties, and the list isn't in any dataset. All of it shows as "other public land: check the managing agency".
- **Local ordinances are missing everywhere except Cambridge,** and Cambridge's section text is unverified. Many cities and towns ban or restrict discharge, and some require written permission to hunt. The map says so for every town.
- **Rail lines, shooting direction and backstops** are not modeled, as in the Cambridge version.
- **Garages and barns count as dwellings** when they are 300 sq ft or more on a residential parcel, because MassGIS footprints have no building type. This errs toward smaller candidates.
- **Buildings with no known parcel use count as dwellings** (0.3% of footprints). This also errs toward smaller candidates.
- **Parcel vintages differ by town** (FY2018–FY2027), and the 1:25k hydrography dates from 2019.
- **The MassDOT surface type is unrecorded** on 118,000 road segments; those are treated as hard-surfaced.
- **Road surfaces come from centerlines.** Each is buffered by half its recorded width, or 12 ft where none is recorded, so a wide road with no width recorded is drawn too narrow by a few feet.
- **Setback waivers aren't modeled.** Both setbacks can be waived by the owner or occupant; the map assumes nobody has.
- **Watershed land needs a DCR permit.** Dropping Zone A puts the Quabbin, Wachusett and Ware River lands back on the map. Hunting there requires DCR's watershed hunting access permit and follows DCR's own area rules.
- **Owner lookups are live, the analysis isn't.** The parcel service returns the current assessing year, which can be newer than the parcels used to build the candidates (FY2018–FY2027 by town), so a lot's owner or boundary can differ slightly from what the analysis saw.
- **Seasons, zones, licenses, legal days and hours, and posted land** are not on the map.

## Reproducing

```bash
uv run python -m turkey ma all          # fetch (~1.6 GB of archives) + prep + run + web
uv run python -m turkey ma run --towns Petersham,Concord   # rerun a few towns
```

See the [README](../README.md) for outputs and publishing.
