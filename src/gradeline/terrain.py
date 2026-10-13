"""Terrain products from a DEM: depressions, flow routing, HAND, distance to stream.

Uses pysheds. (Whitebox Workflows 2.0.6 took ~5 min per step on the full 10 m Onion grid
for no clear reason, vs <1 s on any sub-crop; dropped for now.)

Outputs go to data/interim/<key>/terrain_<res>m/:
- depth_sink_m.tif         filled DEM - DEM (all closed depressions)
                           KNOWN ISSUE: no breaching yet, so this includes false sinks behind
                           road embankments / culverts ("digital dams"). Matters most at 1 m.
- dem_hydro.tif            filled + flat-resolved DEM used for routing
- flow_acc_m2.tif          D8 contributing area (m2)
- streams.tif              flow_acc >= stream_threshold_km2
- hand_m.tif               height above nearest drainage (along D8 flow path)
- dist_stream_m.tif        straight-line distance to the stream cell the HAND path drains to
- upstream_imperv_<year>.tif  mean NLCD impervious % over each cell's contributing area (10 m only)
"""
import copy
import heapq
from pathlib import Path

import numba
import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

from .config import data_dir, load_watershed

if not hasattr(np, "in1d"):  # pysheds 0.4 still calls np.in1d, removed in NumPy 2.5
    np.in1d = np.isin

NODATA = -9999.0


def _prep_dem(src: Path, dst: Path) -> None:
    with rasterio.open(src) as r:
        a = r.read(1)
        bad = ~np.isfinite(a) if r.nodata is None or np.isnan(r.nodata) else (a == r.nodata) | ~np.isfinite(a)
        prof = r.profile | {"nodata": NODATA, "dtype": "float32", "compress": "deflate",
                            "predictor": 3, "tiled": True, "BIGTIFF": "IF_SAFER"}
    with rasterio.open(dst, "w", **prof) as w:
        w.write(np.where(bad, NODATA, a).astype("float32"), 1)


def _write(template: Path, dst: Path, arr: np.ndarray, valid: np.ndarray, dtype="float32") -> None:
    nodata = 0 if dtype == "uint8" else NODATA
    with rasterio.open(template) as r:
        prof = r.profile | {"dtype": dtype, "nodata": nodata, "compress": "deflate",
                            "tiled": True, "BIGTIFF": "IF_SAFER"}
    with rasterio.open(dst, "w", **prof) as w:
        w.write(np.where(valid, arr, nodata).astype(dtype), 1)


def _resample_to(src: Path, template: Path) -> np.ndarray:
    with rasterio.open(template) as t, rasterio.open(src) as s:
        out = np.full((t.height, t.width), np.nan, dtype="float32")
        reproject(s.read(1).astype("float32"), out, src_transform=s.transform, src_crs=s.crs,
                  dst_transform=t.transform, dst_crs=t.crs, src_nodata=s.nodata,
                  dst_nodata=np.nan, resampling=Resampling.nearest)
    return out


@numba.njit(cache=True)
def priority_flood_eps(dem: np.ndarray, nodata: float, eps: float) -> np.ndarray:
    """Barnes et al. (2014) priority-flood + epsilon: fills depressions AND gives flats a
    tiny downhill gradient, so every valid cell drains to the grid edge or a nodata cell.
    (pysheds resolve_flats left ~850 dead-end cells on the 10 m Onion grid.)"""
    nr, nc = dem.shape
    out = dem.astype(np.float64)
    closed = np.zeros((nr, nc), dtype=np.bool_)
    heap = [(0.0, 0, 0)]
    heap.pop()
    k = 0
    for r in range(nr):
        for c in range(nc):
            if dem[r, c] == nodata:
                continue
            edge = r == 0 or c == 0 or r == nr - 1 or c == nc - 1
            if not edge:
                for dr in range(-1, 2):
                    for dc in range(-1, 2):
                        if dem[r + dr, c + dc] == nodata:
                            edge = True
            if edge:
                closed[r, c] = True
                heapq.heappush(heap, (out[r, c], k, r * nc + c))
                k += 1
    while heap:
        z, _, i = heapq.heappop(heap)
        r, c = i // nc, i % nc
        for dr in range(-1, 2):
            for dc in range(-1, 2):
                rr, cc = r + dr, c + dc
                if (dr == 0 and dc == 0) or rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                    continue
                if closed[rr, cc] or dem[rr, cc] == nodata:
                    continue
                closed[rr, cc] = True
                if out[rr, cc] <= z + eps:
                    out[rr, cc] = z + eps
                heapq.heappush(heap, (out[rr, cc], k, rr * nc + cc))
                k += 1
    return out


def build_terrain(key: str, res: int) -> Path:
    from pysheds.grid import Grid
    from pysheds.view import Raster

    ws = load_watershed(key)
    tcfg = {"stream_threshold_km2": 1.0, **ws.get("terrain", {})}
    out = data_dir("interim", key) / f"terrain_{res}m"
    out.mkdir(exist_ok=True)
    tpl = out / "dem.tif"
    _prep_dem(data_dir("raw", key) / f"dem_{res}m.tif", tpl)

    g = Grid.from_raster(str(tpl))
    dem = g.read_raster(str(tpl))
    valid = np.asarray(dem) != NODATA
    cell_m2 = abs(g.affine.a * g.affine.e)

    filled = g.fill_depressions(g.fill_pits(dem))
    _write(tpl, out / "depth_sink_m.tif", np.asarray(filled) - np.asarray(dem), valid)
    hydro_arr = priority_flood_eps(np.asarray(dem, dtype=np.float64), NODATA, 1e-4)
    _write(tpl, out / "dem_hydro.tif", hydro_arr, valid)
    hydro = Raster(hydro_arr, viewfinder=dem.viewfinder)

    fdir = g.flowdir(hydro)
    acc = g.accumulation(fdir)
    acc_m2 = np.asarray(acc) * cell_m2
    _write(tpl, out / "flow_acc_m2.tif", acc_m2, valid)
    streams = valid & (acc_m2 >= tcfg["stream_threshold_km2"] * 1e6)
    ten = data_dir("interim", key) / "terrain_10m" / "streams.tif"
    if res < 10 and ten.exists():
        # A sub-watershed DEM (the 1 m pilot box) truncates upstream area where big creeks enter,
        # so their channels never reach the threshold locally. Borrow the 10 m stream network
        # and snap it to local 1 m channel cells.
        s10 = np.nan_to_num(_resample_to(ten, tpl)) > 0
        streams |= valid & s10 & (acc_m2 >= tcfg.get("stream_snap_min_m2", 2000))
    _write(tpl, out / "streams.tif", streams, valid, "uint8")

    def as_raster(a, nodata=NODATA):
        vf = copy.deepcopy(dem.viewfinder)
        vf.nodata = nodata
        return Raster(a, viewfinder=vf)

    streams_r = as_raster(streams, nodata=False)
    hand = g.compute_hand(fdir, hydro, streams_r)
    _write(tpl, out / "hand_m.tif", np.asarray(hand), valid & np.isfinite(np.asarray(hand)))

    # Distance to the specific stream cell each cell's flow path reaches.
    idx = np.asarray(g.compute_hand(fdir, hydro, streams_r, return_index=True))
    ok = valid & (idx >= 0)
    rows, cols = np.indices(idx.shape)
    sr, sc = np.divmod(np.where(ok, idx, 0), idx.shape[1])
    dist = np.hypot((rows - sr) * g.affine.e, (cols - sc) * g.affine.a)
    _write(tpl, out / "dist_stream_m.tif", dist, ok)
    # Size of the creek each cell drains to: lets HAND be judged against that creek's flood depth.
    _write(tpl, out / "drain_to_area_m2.tif", acc_m2.ravel()[np.where(ok, idx, 0).ravel()].reshape(idx.shape), ok)

    if res >= 10:
        # Backwater: a house can drain first to a small tributary but flood from the big creek
        # backing up into it. HAND against coarser networks measures height above the big creek.
        for t_km2 in tcfg.get("hand_scales_km2", []):
            s_t = as_raster(valid & (acc_m2 >= t_km2 * 1e6), nodata=False)
            h_t = np.asarray(g.compute_hand(fdir, hydro, s_t))
            i_t = np.asarray(g.compute_hand(fdir, hydro, s_t, return_index=True))
            ok_t = valid & (i_t >= 0) & np.isfinite(h_t)
            _write(tpl, out / f"hand_m_{t_km2:g}km2.tif", h_t, ok_t)
            _write(tpl, out / f"drain_to_area_m2_{t_km2:g}km2.tif",
                   acc_m2.ravel()[np.where(ok_t, i_t, 0).ravel()].reshape(i_t.shape), ok_t)
        for y in ws["nlcd_years"]["impervious"]:
            imp = _resample_to(data_dir("raw", key) / f"nlcd_impervious_{y}.tif", tpl)
            w = np.where(valid & np.isfinite(imp), imp, 0.0)
            upstream = np.asarray(g.accumulation(fdir, weights=as_raster(w.astype("float64")))) / np.maximum(np.asarray(acc), 1)
            _write(tpl, out / f"upstream_imperv_{y}.tif", upstream, valid)
    return out
