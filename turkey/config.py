import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQFT_PER_ACRE = 43560.0


def load(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    p = cfg["project"]
    for key in ("raw_dir", "out_dir", "gpkg"):
        p[key] = ROOT / p[key]
    return cfg
