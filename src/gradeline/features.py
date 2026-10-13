"""Per-building features -> data/processed/<key>/buildings_features.parquet.

Terrain features use the 1 m pilot where a building falls inside it, else 10 m.
`terrain_res` records which one, so the score can lower confidence on 10 m.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from exactextract import exact_extract

from .config import data_dir, load_watershed

# raster -> (stat, output column). Buffers are around the footprint.
# Micro-topography only; 1 m overrides 10 m where available.
TERRAIN_STATS = {"depth_sink_m.tif": ("max", "depression_depth_m")}


def load_buildings(key: str) -> gpd.GeoDataFrame:
    ws = load_watershed(key)
    min_area = ws.get("min_footprint_m2", 40)
    b = gpd.read_parquet(data_dir("raw", key) / "buildings_overture.parquet").to_crs(ws["crs"])
    aoi = gpd.read_parquet(data_dir("interim", key) / "aoi.parquet").to_crs(ws["crs"])
    b = b[b.representative_point().within(aoi.geometry.iloc[0])]  # score only buildings inside the AOI
    b = b.assign(footprint_m2=b.area)
    b = b[b.footprint_m2 >= min_area].reset_index(drop=True)
    b["source"] = b.sources.map(lambda s: s[0]["dataset"] if s is not None and len(s) else None)
    return b[["id", "source", "height", "num_floors", "class", "footprint_m2", "geometry"]]


def _zonal(polys: gpd.GeoDataFrame, raster, stat: str) -> np.ndarray:
    df = exact_extract(str(raster), polys[["geometry"]], [stat], output="pandas")
    return df[stat].to_numpy(dtype="float64")


def terrain_features(b: gpd.GeoDataFrame, key: str, res: int, buffer_m: float) -> pd.DataFrame:
    tdir = data_dir("interim", key) / f"terrain_{res}m"
    zones = b[["geometry"]].copy()
    zones["geometry"] = zones.buffer(buffer_m)
    out = pd.DataFrame(index=b.index)
    for fname, (stat, col) in TERRAIN_STATS.items():
        out[col] = _zonal(zones, tdir / fname, stat)
    return out


def _in_raster(b: gpd.GeoDataFrame, raster) -> np.ndarray:
    with rasterio.open(raster) as r:
        w, s, e, n = r.bounds
    pts = b.representative_point()
    return ((pts.x > w) & (pts.x < e) & (pts.y > s) & (pts.y < n)).to_numpy()


def impervious_features(b: gpd.GeoDataFrame, key: str, years: list[int], radius_m: float) -> pd.DataFrame:
    raw = data_dir("raw", key)
    zones = gpd.GeoDataFrame(geometry=b.representative_point().buffer(radius_m), crs=b.crs)
    out = pd.DataFrame(index=b.index)
    for y in years:
        out[f"imperv_{radius_m:.0f}m_{y}"] = _zonal(zones, raw / f"nlcd_impervious_{y}.tif", "mean")
    out[f"imperv_{radius_m:.0f}m_change"] = out.iloc[:, -1] - out.iloc[:, 0]
    return out


def upstream_features(b: gpd.GeoDataFrame, key: str, buffer_m: float) -> pd.DataFrame:
    """Upstream area and upstream impervious % at the cell with the largest drainage area
    within `buffer_m` of the footprint (the flow path that reaches the building), plus HAND,
    distance to stream and drained-to creek size at the lowest-HAND cell.
    Always from 10 m: the 1 m pilot box truncates upstream area, and 1 m HAND without
    breaching follows road embankments on long detours."""
    tdir = data_dir("interim", key) / "terrain_10m"
    with rasterio.open(tdir / "flow_acc_m2.tif") as r:
        acc, inv = r.read(1, masked=True).filled(-1), ~r.transform
    with rasterio.open(tdir / "hand_m.tif") as r:
        hand = r.read(1, masked=True).filled(np.inf)
    scales = {}
    for p in sorted(tdir.glob("hand_m_*km2.tif")):
        tag = p.stem.removeprefix("hand_m_")
        with rasterio.open(p) as r, rasterio.open(tdir / f"drain_to_area_m2_{tag}.tif") as r2:
            scales[tag] = (r.read(1, masked=True).filled(np.inf), r2.read(1, masked=True).filled(np.nan))
    side = {}
    for name in ["drain_to_area_m2", "dist_stream_m"]:
        with rasterio.open(tdir / f"{name}.tif") as r:
            side[name] = r.read(1, masked=True).filled(np.nan)
    imp = {}
    for p in sorted(tdir.glob("upstream_imperv_*.tif")):
        with rasterio.open(p) as r:
            imp[p.stem] = r.read(1)

    nr, nc = acc.shape
    scale_cols = [f"{c}_{t}" for t in scales for c in ("hand_m", "drain_to_area_m2")]
    rows = {k: np.full(len(b), np.nan) for k in ["upstream_area_m2", *imp, "hand_m", *side, *scale_cols]}
    for i, (x0, y0, x1, y1) in enumerate(b.buffer(buffer_m).bounds.to_numpy()):
        c0, r0 = inv * (x0, y1)
        c1, r1 = inv * (x1, y0)
        r0, c0 = max(int(r0), 0), max(int(c0), 0)
        r1, c1 = min(int(np.ceil(r1)), nr), min(int(np.ceil(c1)), nc)
        win = acc[r0:r1, c0:c1]
        if win.size == 0 or win.max() < 0:
            continue
        rr, cc = np.unravel_index(win.argmax(), win.shape)
        rows["upstream_area_m2"][i] = win[rr, cc]
        for k, a in imp.items():
            rows[k][i] = a[r0 + rr, c0 + cc]
        # Riverine: the lowest-HAND cell touching the building (10 m; validated vs USGS gauges).
        hw = hand[r0:r1, c0:c1]
        if np.isfinite(hw.min()):
            hr, hc = np.unravel_index(hw.argmin(), hw.shape)
            rows["hand_m"][i] = hw[hr, hc]
            for k, a in side.items():
                rows[k][i] = a[r0 + hr, c0 + hc]
        for t, (h_t, a_t) in scales.items():
            hw = h_t[r0:r1, c0:c1]
            if np.isfinite(hw.min()):
                hr, hc = np.unravel_index(hw.argmin(), hw.shape)
                rows[f"hand_m_{t}"][i] = hw[hr, hc]
                rows[f"drain_to_area_m2_{t}"][i] = a_t[r0 + hr, c0 + hc]
    out = pd.DataFrame(rows, index=b.index)
    years = sorted(imp)
    if len(years) > 1:
        out["upstream_imperv_change"] = out[years[-1]] - out[years[0]]
    return out


def soils_fema(b: gpd.GeoDataFrame, key: str) -> pd.DataFrame:
    raw = data_dir("raw", key)
    pts = gpd.GeoDataFrame(geometry=b.representative_point(), crs=b.crs)
    soils = gpd.read_parquet(raw / "ssurgo.parquet").to_crs(b.crs)
    fema = gpd.read_parquet(raw / "fema_nfhl_zones.parquet").to_crs(b.crs)

    s = gpd.sjoin(pts, soils[["hydgrpdcd", "drclassdcd", "wtdepannmin", "geometry"]],
                  how="left", predicate="within")
    s = s[~s.index.duplicated()]
    f = gpd.sjoin(pts, fema[["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF", "geometry"]],
                  how="left", predicate="within")
    # Overlapping NFHL polygons: keep the most hazardous (SFHA first).
    f = f.sort_values("SFHA_TF", ascending=False, na_position="last")
    f = f[~f.index.duplicated()].sort_index()

    sfha = fema[fema.SFHA_TF == "T"]
    dist = gpd.sjoin_nearest(pts, sfha[["geometry"]], how="left", distance_col="d")
    dist = dist[~dist.index.duplicated()]["d"]

    return pd.DataFrame({
        "hsg": s["hydgrpdcd"], "drainage_class": s["drclassdcd"],
        # SSURGO leaves wtdepannmin null when the water table is >200 cm: treat as 201 (deep).
        "water_table_min_cm": pd.to_numeric(s["wtdepannmin"], errors="coerce").fillna(201),
        "fema_zone": f["FLD_ZONE"],
        "fema_zone_subtype": f["ZONE_SUBTY"],
        "in_sfha": f["SFHA_TF"].eq("T"),
        "dist_sfha_m": dist,
    }, index=b.index)


def add_hand_ratio(f: pd.DataFrame, key: str) -> pd.DataFrame:
    """HAND / expected 100-yr depth for the creek the building drains to (FEMA-calibrated)."""
    import json

    from .calibrate import calibrate_hand, expected_depth

    p = data_dir("interim", key) / "hand_calibration.json"
    cal = json.loads(p.read_text()) if p.exists() else calibrate_hand(key)
    # Ratio at each stream-network scale; the worst (smallest) one wins, and its HAND, creek
    # size and depth are reported so the driver names the creek that actually threatens.
    tags = [""] + sorted({c.removeprefix("hand_m_") for c in f if c.startswith("hand_m_")})
    ratios, depths = {}, {}
    for t in tags:
        sfx = f"_{t}" if t else ""
        depths[t] = expected_depth(f[f"drain_to_area_m2{sfx}"], cal)
        ratios[t] = (f[f"hand_m{sfx}"] / depths[t]).to_numpy()
    R = np.vstack([ratios[t] for t in tags])
    best = np.argmin(np.where(np.isfinite(R), R, np.inf), axis=0)
    pick = lambda d: np.choose(best, [np.asarray(d[t], dtype=float) for t in tags])  # noqa: E731
    f["hand_ratio"] = pick(ratios)
    f["flood_depth_100yr_est_m"] = pick(depths)
    f["riverine_hand_m"] = pick({t: f[f"hand_m_{t}" if t else "hand_m"] for t in tags})
    f["riverine_creek_area_km2"] = pick({t: f[f"drain_to_area_m2_{t}" if t else "drain_to_area_m2"] for t in tags}) / 1e6
    f.loc[np.isnan(R).all(axis=0), "hand_ratio"] = np.nan
    return f


def build_features(key: str) -> gpd.GeoDataFrame:
    ws = load_watershed(key)
    fcfg = {"buffer_10m": 10, "buffer_1m": 3, "imperv_radius_m": 250, **ws.get("features", {})}
    b = load_buildings(key)

    t = terrain_features(b, key, 10, fcfg["buffer_10m"]).assign(terrain_res=10)
    one_m = data_dir("interim", key) / "terrain_1m" / "hand_m.tif"
    if one_m.exists():
        inside = _in_raster(b, one_m)
        sub = b[inside]
        t1 = terrain_features(sub, key, 1, fcfg["buffer_1m"]).assign(terrain_res=1)
        t.loc[t1.index, t1.columns] = t1

    imp = impervious_features(b, key, ws["nlcd_years"]["impervious"], fcfg["imperv_radius_m"])
    up = upstream_features(b, key, fcfg["buffer_10m"])
    sf = soils_fema(b, key)
    out = pd.concat([b, t, imp, up, sf], axis=1)
    out = add_hand_ratio(out, key)
    out.to_parquet(data_dir("processed", key) / "buildings_features.parquet")
    return out
