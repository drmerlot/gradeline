"""Transparent 0-100 water-risk score from per-building features (config/scoring.yaml)."""
import geopandas as gpd
import numpy as np
import pandas as pd
import yaml

from .config import ROOT, data_dir


def load_rules() -> dict:
    return yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text())


def _derive(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["upstream_area_km2"] = f["upstream_area_m2"] / 1e6
    shaded = f["fema_zone_subtype"].fillna("").str.contains("0.2 PCT")
    f["fema_zone_class"] = np.select([f["in_sfha"], shaded], ["in the FEMA 100-year floodplain", "in the FEMA 500-year (shaded X) zone"], "none")
    return f


def _rule_points(vals: pd.Series, rule: dict) -> pd.Series:
    pts = pd.Series(0, index=vals.index, dtype="int64")
    done = pd.Series(False, index=vals.index)
    for thr, p in rule["tiers"]:
        if rule["op"] == "lt":
            hit = vals < thr
        elif rule["op"] == "ge":
            hit = vals >= thr
        else:
            hit = vals == thr
        hit = hit.fillna(False) & ~done
        pts[hit] = p
        done |= hit
    return pts


def _confidence(f: pd.DataFrame) -> pd.Series:
    level = np.where(f["terrain_res"] == 1, 2, 1)  # 2 high, 1 medium, 0 low
    weak = f["fema_zone"].isna() | f["fema_zone"].eq("A") | f["hand_m"].isna()
    level = np.where(weak, level - 1, level)
    return pd.Series(np.array(["low", "medium", "high"])[np.clip(level, 0, 2)], index=f.index)


def score(features: pd.DataFrame, rules: dict | None = None) -> pd.DataFrame:
    rules = rules or load_rules()
    f = _derive(features)
    parts = pd.DataFrame({name: _rule_points(f[r["feature"]], r) for name, r in rules["rules"].items()})
    f["score"] = parts.sum(axis=1).clip(upper=100)
    for name in parts:
        f[f"pts_{name}"] = parts[name]

    def drivers(i):
        hits = sorted(((parts.at[i, n], n) for n in parts if parts.at[i, n] > 0), reverse=True)
        out = []
        for _, n in hits:
            r = rules["rules"][n]
            extra = {k: f.at[i, k] for k in r.get("extra", [])}
            out.append(r["driver"].format(v=f.at[i, r["feature"]], **extra))
        return out

    f["drivers"] = [drivers(i) for i in f.index]
    f["outside_fema_but_risky"] = ~f["in_sfha"] & (f["score"] >= rules["outside_fema_but_risky_min_score"])
    f["confidence"] = _confidence(f)
    f["score_version"] = rules["version"]
    return f


def build_scores(key: str) -> gpd.GeoDataFrame:
    f = gpd.read_parquet(data_dir("processed", key) / "buildings_features.parquet")
    s = gpd.GeoDataFrame(score(f), geometry="geometry", crs=f.crs)
    s.to_parquet(data_dir("processed", key) / "buildings_scored.parquet")
    return s
