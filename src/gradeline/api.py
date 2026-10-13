"""Local water-risk API.

    uv run uvicorn gradeline.api:app --reload
    curl -H "X-API-Key: dev-local" "localhost:8000/water-risk?address=..."

Scores are precomputed (data/processed/<key>/buildings_scored.parquet); this only looks up.
Every call is logged per API key to data/usage.sqlite (the basis for per-property billing).
"""
import os
import sqlite3
import time
from functools import lru_cache

import geopandas as gpd
import pandas as pd
import requests
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from shapely.geometry import Point

from .config import DATA, ROOT

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
MATCH_MAX_M = 60  # Census points are interpolated along the street; buildings sit back from it

app = FastAPI(title="Gradeline water risk", version="0.0.1",
              description="Draft building-level water risk. Not a flood determination.")


# ---------- auth + metering ----------

@lru_cache
def _keys() -> dict:
    """config/api_keys.yaml (gitignored): {key: {name, daily_cap}}. Falls back to one dev key."""
    p = ROOT / "config" / "api_keys.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text()) or {}
    return {os.environ.get("GRADELINE_DEV_KEY", "dev-local"): {"name": "dev", "daily_cap": 10_000}}


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DATA / "usage.sqlite")
    con.execute("""CREATE TABLE IF NOT EXISTS usage (
        ts REAL, api_key TEXT, endpoint TEXT, query TEXT, building_id TEXT, billable INTEGER)""")
    return con


def require_key(x_api_key: str | None = Header(None)) -> str:
    meta = _keys().get(x_api_key) if x_api_key else None
    if meta is None:
        raise HTTPException(401, "invalid API key")
    with _db() as con:
        n = con.execute("SELECT COUNT(*) FROM usage WHERE api_key=? AND ts>? AND billable=1",
                        (x_api_key, time.time() - 86400)).fetchone()[0]
    if n >= meta.get("daily_cap", 1000):
        raise HTTPException(429, "daily cap reached")
    return x_api_key


def _log(key: str, endpoint: str, query: str, building_id: str | None) -> None:
    with _db() as con:
        con.execute("INSERT INTO usage VALUES (?,?,?,?,?,?)",
                    (time.time(), key, endpoint, query, building_id, int(building_id is not None)))


# ---------- data ----------

@lru_cache
def _buildings() -> gpd.GeoDataFrame:
    parts = [gpd.read_parquet(p) for p in sorted((DATA / "processed").glob("*/buildings_scored.parquet"))]
    if not parts:
        raise RuntimeError("no scored buildings; run `gradeline score <watershed>`")
    b = pd.concat(parts, ignore_index=True)
    b = gpd.GeoDataFrame(b, geometry="geometry", crs=parts[0].crs)
    b.sindex  # build once
    return b


def geocode(address: str) -> tuple[float, float, str]:
    r = requests.get(CENSUS, params={"address": address, "benchmark": "Public_AR_Current",
                                     "format": "json"}, timeout=20)
    r.raise_for_status()
    m = r.json()["result"]["addressMatches"]
    if not m:
        raise HTTPException(404, f"address not found by Census geocoder: {address!r}")
    c = m[0]["coordinates"]
    return c["y"], c["x"], m[0]["matchedAddress"]


def match_building(lat: float, lon: float) -> tuple[pd.Series, str, float]:
    b = _buildings()
    pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(b.crs).iloc[0]
    hit = b.sindex.query(pt, predicate="within")
    if len(hit):
        return b.iloc[hit[0]], "inside_footprint", 0.0
    idx, dist = b.sindex.nearest(pt, max_distance=MATCH_MAX_M, return_distance=True)
    if idx.shape[1] == 0:
        raise HTTPException(404, f"no scored building within {MATCH_MAX_M} m of this location "
                                 "(outside coverage, or no footprint)")
    return b.iloc[idx[1][0]], "nearest_footprint", float(dist[0])


def _fmt(row: pd.Series, how: str, dist_m: float) -> dict:
    conf = row["confidence"]
    if how == "nearest_footprint" and conf == "high":
        conf = "medium"  # we may have picked the neighbor's house
    num = lambda v, n=2: None if pd.isna(v) else round(float(v), n)  # noqa: E731
    return {
        "score": int(row["score"]),
        "fema_zone": None if pd.isna(row["fema_zone"]) else row["fema_zone"],
        "outside_fema_but_risky": bool(row["outside_fema_but_risky"]),
        "drivers": list(row["drivers"]),
        "confidence": conf,
        "building": {"id": row["id"], "match": how, "match_distance_m": round(dist_m, 1),
                     "footprint_source": row["source"], "terrain_resolution_m": int(row["terrain_res"])},
        "features": {
            "hand_m": num(row["hand_m"]), "depression_depth_m": num(row["depression_depth_m"]),
            "dist_stream_m": num(row["dist_stream_m"], 0),
            "upstream_area_km2": num(row["upstream_area_m2"] / 1e6, 3),
            "hydrologic_soil_group": row["hsg"],
            "impervious_250m_2001": num(row["imperv_250m_2001"], 1),
            "impervious_250m_2021": num(row["imperv_250m_2021"], 1),
            "upstream_impervious_2001": num(row.get("upstream_imperv_2001"), 1),
            "upstream_impervious_2021": num(row.get("upstream_imperv_2021"), 1),
            "dist_to_fema_sfha_m": num(row["dist_sfha_m"], 0),
        },
        "points": {k[4:]: int(v) for k, v in row.items() if k.startswith("pts_")},
        "score_version": row["score_version"],
        "disclaimer": "Draft screening estimate, not a flood zone determination or engineering opinion.",
    }


# ---------- routes ----------

@app.get("/health")
def health():
    return {"ok": True, "buildings": len(_buildings())}


@app.get("/water-risk")
def water_risk(address: str | None = None, lat: float | None = Query(None), lon: float | None = Query(None),
               key: str = Depends(require_key)):
    if address:
        lat, lon, matched = geocode(address)
    elif lat is not None and lon is not None:
        matched = None
    else:
        raise HTTPException(400, "pass address=... or lat=...&lon=...")
    try:
        row, how, d = match_building(lat, lon)
    except HTTPException:
        _log(key, "/water-risk", address or f"{lat},{lon}", None)
        raise
    _log(key, "/water-risk", address or f"{lat},{lon}", row["id"])
    return {"query": {"address": address, "matched_address": matched, "lat": lat, "lon": lon},
            **_fmt(row, how, d)}
