"""Calibrate expected 100-yr flood depth above channel vs creek size, from FEMA's own AE zones.

For cells on the edge of FEMA AE (detailed-study) floodplains, HAND ~= the 100-yr flood depth
on that reach. Fit depth = a * A_km2^b on bin medians, where A = drainage area of the creek
the cell drains to. Zone A (approximate) is excluded.
"""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from scipy.ndimage import binary_erosion

from .config import data_dir


def calibrate_hand(key: str) -> dict:
    t = data_dir("interim", key) / "terrain_10m"
    with rasterio.open(t / "hand_m.tif") as r:
        hand, tr, shape, crs = r.read(1, masked=True), r.transform, r.shape, r.crs
    with rasterio.open(t / "drain_to_area_m2.tif") as r:
        area = r.read(1, masked=True)
    fema = gpd.read_parquet(data_dir("raw", key) / "fema_nfhl_zones.parquet").to_crs(crs)
    ae = rasterize(((g, 1) for g in fema[fema.FLD_ZONE == "AE"].geometry), out_shape=shape,
                   transform=tr, fill=0) == 1
    edge = ae & ~binary_erosion(ae) & ~hand.mask & ~area.mask & (area.filled(0) >= 1e6)
    d = pd.DataFrame({"A_km2": area[edge] / 1e6, "hand": hand[edge]})
    d["bin"] = pd.cut(np.log10(d.A_km2), np.arange(0, 4.01, 0.25))
    g = d.groupby("bin", observed=True).agg(A=("A_km2", "median"), h=("hand", "median"), n=("hand", "size"))
    g = g[g.n >= 200]
    b, a = np.polyfit(np.log10(g.A), np.log10(g.h), 1)
    cal = {"a": round(10 ** a, 4), "b": round(b, 4), "n_cells": int(len(d)), "floor_m": 1.0,
           "bins": [{"A_km2": round(r.A, 2), "median_hand_m": round(r.h, 2), "n": int(r.n)}
                    for r in g.itertuples()]}
    (data_dir("interim", key) / "hand_calibration.json").write_text(json.dumps(cal, indent=2))
    return cal


def expected_depth(area_m2, cal: dict):
    return np.maximum(cal["a"] * (np.asarray(area_m2, dtype=float) / 1e6) ** cal["b"], cal["floor_m"])
