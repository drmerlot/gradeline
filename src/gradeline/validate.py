"""Sanity checks against independent data (USGS gauge drainage areas)."""
import geopandas as gpd
import rasterio
from rasterio.windows import from_bounds

from .config import data_dir, load_watershed

SQMI = 2.589988e6


def gauge_drainage_check(key: str, res: int = 10, search_m: float = 150) -> list[dict]:
    ws = load_watershed(key)
    gauges = ws.get("validation_gauges", {})
    pts = gpd.GeoSeries(gpd.points_from_xy([g["lon"] for g in gauges.values()],
                                           [g["lat"] for g in gauges.values()]), crs=4326).to_crs(ws["crs"])
    rows = []
    with rasterio.open(data_dir("interim", key) / f"terrain_{res}m" / "flow_acc_m2.tif") as r:
        for (site, g), p in zip(gauges.items(), pts):
            w = from_bounds(p.x - search_m, p.y - search_m, p.x + search_m, p.y + search_m, r.transform)
            model = float(r.read(1, window=w, masked=True).max()) / SQMI
            rows.append({"site": site, "name": g["name"], "usgs_mi2": g["da_mi2"],
                         "model_mi2": round(model, 1), "pct_err": round(100 * (model / g["da_mi2"] - 1), 1)})
    return rows
