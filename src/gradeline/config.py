from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
MAPS = ROOT / "maps"


def load_watershed(key: str) -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "watersheds.yaml").read_text())
    if key not in cfg:
        raise KeyError(f"{key!r} not in config/watersheds.yaml (have: {list(cfg)})")
    return {"key": key, **cfg[key]}


def data_dir(stage: str, key: str) -> Path:
    p = DATA / stage / key
    p.mkdir(parents=True, exist_ok=True)
    return p
