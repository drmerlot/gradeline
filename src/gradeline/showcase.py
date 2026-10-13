"""Backtest: would the score have flagged homes that were later bought out?

Probable buyouts = City of Austin 2013 footprints (>= 80 m2) inside the 1 m pilot box that no
current Overture footprint overlaps. Caveat: also catches unrelated demolitions and footprints
missing from Overture.
"""
import geopandas as gpd
import pandas as pd

from . import features as F
from .config import data_dir, load_watershed
from .score import score


def probable_buyouts(key: str) -> gpd.GeoDataFrame:
    ws = load_watershed(key)
    old = gpd.read_parquet(data_dir("raw", key) / "austin_footprints_2013.parquet").to_crs(ws["crs"])
    old = old[(old.feature == "Structure") & (old.area >= 80)]
    new = gpd.read_parquet(data_dir("raw", key) / "buildings_overture.parquet").to_crs(ws["crs"])
    hit = gpd.sjoin(old[["objectid", "geometry"]], new[["geometry"]], predicate="intersects")
    gone = old[~old.objectid.isin(hit.objectid)]
    return gpd.GeoDataFrame({"id": "coa2013_" + gone.objectid.astype(str), "source": "CoA 2013",
                             "height": None, "num_floors": None, "class": None,
                             "footprint_m2": gone.area}, geometry=gone.geometry, crs=ws["crs"]).reset_index(drop=True)


def backtest(key: str) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    ws = load_watershed(key)
    fc = {"buffer_10m": 10, "buffer_1m": 3, "imperv_radius_m": 250, **ws.get("features", {})}
    g = probable_buyouts(key)
    t = F.terrain_features(g, key, 1, fc["buffer_1m"]).assign(terrain_res=1)
    feats = pd.concat([g, t, F.impervious_features(g, key, ws["nlcd_years"]["impervious"], fc["imperv_radius_m"]),
                       F.upstream_features(g, key, fc["buffer_10m"]), F.soils_fema(g, key)], axis=1)
    feats = F.add_hand_ratio(feats, key)
    gone = gpd.GeoDataFrame(score(feats), geometry="geometry", crs=g.crs).assign(group="probable buyout (gone since 2013)")

    cur = gpd.read_parquet(data_dir("processed", key) / "buildings_scored.parquet")
    cur = cur[cur.terrain_res == 1].assign(group="still standing (same 1 m area)")
    both = pd.concat([gone, cur], ignore_index=True)

    def summ(d):
        out_f = ~d.in_sfha
        return pd.Series({
            "n": len(d), "median_score": d.score.median(),
            "pct_score_ge40": round(100 * (d.score >= 40).mean(), 1),
            "pct_in_sfha": round(100 * d.in_sfha.mean(), 1),
            "n_outside_sfha": int(out_f.sum()),
            "pct_of_outside_flagged_ge40": round(100 * (d[out_f].score >= 40).mean(), 1),
        })
    table = both.groupby("group").apply(summ)
    gone.to_parquet(data_dir("processed", key) / "backtest_probable_buyouts.parquet")
    return table, gone
