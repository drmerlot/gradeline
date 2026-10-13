"""Terrain products from a DEM: depressions, flow routing, HAND, distance to stream.

Outputs go to data/interim/<key>/terrain_<res>m/:
- depth_sink_raw.tif       fill(DEM) - DEM. Every closed depression, including false ones
                           behind road embankments / culverts ("digital dams").
- depth_sink.tif           depressions that survive least-cost breaching. Closer to where
                           water actually ponds; use this one for scoring.
- dem_hydro.tif            breached + filled DEM used for routing
- d8_pointer.tif, flow_acc_m2.tif (contributing area, m2)
- streams.tif              flow_acc >= stream_threshold_km2
- hand_m.tif               height above nearest drainage (along flow path)
- dist_stream_m.tif        downslope flow-path distance to stream
"""
from pathlib import Path

import numpy as np
import rasterio

from .config import data_dir, load_watershed


def build_terrain(key: str, res: int) -> Path:
    import whitebox_workflows as wbw

    ws = load_watershed(key)
    tcfg = {"breach_max_dist_m": 100, "stream_threshold_km2": 1.0, **ws.get("terrain", {})}
    src = data_dir("raw", key) / f"dem_{res}m.tif"
    out = data_dir("interim", key) / f"terrain_{res}m"
    out.mkdir(exist_ok=True)

    e = wbw.WbEnvironment()
    e.verbose = False
    save = lambda r, name: e.write_raster(r, str(out / name), compress=True)  # noqa: E731

    dem = e.read_raster(str(src))
    max_dist = max(5, int(tcfg["breach_max_dist_m"] / res))

    save(e.depth_in_sink(dem, zero_background=True), "depth_sink_raw.tif")
    breached = e.breach_depressions_least_cost(dem, max_dist=max_dist, fill_deps=False)
    save(e.depth_in_sink(breached, zero_background=True), "depth_sink.tif")

    hydro = e.fill_depressions(breached, fix_flats=True)
    save(hydro, "dem_hydro.tif")
    ptr = e.d8_pointer(hydro)
    save(ptr, "d8_pointer.tif")
    acc = e.d8_flow_accum(ptr, out_type="ca", input_is_pointer=True)
    save(acc, "flow_acc_m2.tif")

    # Stream mask via rasterio so the threshold logic is plain numpy.
    with rasterio.open(out / "flow_acc_m2.tif") as r:
        a = r.read(1, masked=True)
        prof = r.profile | {"dtype": "uint8", "nodata": 0, "compress": "deflate"}
    streams = np.where(~a.mask & (a.filled(0) >= tcfg["stream_threshold_km2"] * 1e6), 1, 0)
    with rasterio.open(out / "streams.tif", "w", **prof) as w:
        w.write(streams.astype("uint8"), 1)

    st = e.read_raster(str(out / "streams.tif"))
    save(e.elevation_above_stream(hydro, st), "hand_m.tif")
    save(e.downslope_distance_to_stream(hydro, st), "dist_stream_m.tif")
    return out
