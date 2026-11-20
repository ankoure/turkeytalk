import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQFT_PER_ACRE = 43560.0
FT = 0.3048  # meters per foot, for the MassGIS (EPSG:26986) pipeline


def load(path: Path | None = None) -> dict:
    """Cambridge city-data pipeline (config.toml)."""
    with open(path or ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    p = cfg["project"]
    for key in ("raw_dir", "out_dir", "gpkg"):
        p[key] = ROOT / p[key]
    return cfg


def load_ma(path: Path | None = None) -> dict:
    """Statewide MassGIS pipeline (massachusetts.toml)."""
    with open(path or ROOT / "massachusetts.toml", "rb") as f:
        cfg = tomllib.load(f)
    p = cfg["project"]
    for key in ("raw_dir", "work_gpkg", "out_dir", "web_dir"):
        p[key] = ROOT / p[key]
    return cfg
