"""Download raw layers for a watershed into data/raw/<key>/.

Every fetcher reads the buffered AOI written by `gradeline boundary` and is safe to re-run
(skips files that already exist unless force=True).
"""
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely import wkt

from .config import data_dir, load_watershed

NFHL_ZONES = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
SDA = "https://sdmdataaccess.sc.egov.usda.gov/tabular/post.rest"


def _aoi(key: str, buffered: bool = True) -> gpd.GeoDataFrame:
    name = "aoi_buffered.parquet" if buffered else "aoi.parquet"
    return gpd.read_parquet(data_dir("interim", key) / name)


def _pilot_geom(key: str):
    """1 m pilot area (config `pilot_1m_bbox`, lon/lat) in the project CRS."""
    from shapely.geometry import box

    ws = load_watershed(key)
    return gpd.GeoSeries([box(*ws["pilot_1m_bbox"])], crs=4326).to_crs(ws["crs"]).iloc[0]


def _save_da(da, path: Path, crs: str, res: float, categorical: bool = False) -> None:
    from rasterio.enums import Resampling

    rs = Resampling.nearest if categorical else Resampling.bilinear
    da = da.rio.reproject(crs, resolution=res, resampling=rs)
    da.rio.to_raster(path, compress="deflate", tiled=True, predictor=3 if da.dtype.kind == "f" else 2)


def fetch_dem(key: str, res: int, force: bool = False) -> Path:
    import py3dep

    ws = load_watershed(key)
    out = data_dir("raw", key) / f"dem_{res}m.tif"
    if out.exists() and not force:
        return out
    if res < 10:
        return fetch_dem_1m_tiles(key, out)
    dem = py3dep.get_dem(_aoi(key).geometry.iloc[0], res, crs=4326)
    _save_da(dem, out, ws["crs"], res)
    return out


TNM_PRODUCTS = "https://tnmaccess.nationalmap.gov/api/v1/products"


def fetch_dem_1m_tiles(key: str, out: Path) -> Path:
    """Windowed read of USGS 1 m DEM COG tiles over the pilot area (no full-tile downloads).

    The 3DEP WMS route (py3dep at 1 m) timed out after caching 1.5 GB, so don't use it.
    """
    import rasterio
    from rasterio.merge import merge

    ws = load_watershed(key)
    geom = _pilot_geom(key)
    w, s, e, n = gpd.GeoSeries([geom], crs=ws["crs"]).to_crs(4326).total_bounds
    items = requests.get(TNM_PRODUCTS, params={
        "datasets": "Digital Elevation Model (DEM) 1 meter", "bbox": f"{w},{s},{e},{n}",
        "prodFormats": "GeoTIFF", "max": 200}, timeout=60).json()["items"]
    # Newest project wins where tiles overlap (merge uses first-listed on top).
    items.sort(key=lambda i: i["publicationDate"], reverse=True)
    urls = ["/vsicurl/" + i["downloadURL"] for i in items]
    (out.parent / "dem_1m_sources.txt").write_text("\n".join(i["title"] for i in items))
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="5",
                      GDAL_HTTP_RETRY_DELAY="5", CPL_VSIL_CURL_CACHE_SIZE="200000000",
                      GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", GDAL_HTTP_MULTIPLEX="YES"):
        srcs = [rasterio.open(u) for u in urls]
        if any(str(r.crs) != ws["crs"] for r in srcs):
            raise ValueError(f"1 m tiles not all in {ws['crs']}: {{r.crs for r in srcs}}")
        merge(srcs, bounds=tuple(round(v) for v in geom.bounds), res=1.0, nodata=-9999.0, dst_path=out,
              dst_kwds={"compress": "deflate", "predictor": 3, "tiled": True,
                        "blockxsize": 512, "blockysize": 512, "BIGTIFF": "IF_SAFER"})
    return out


def fetch_nlcd(key: str, force: bool = False) -> list[Path]:
    import pygeohydro

    ws = load_watershed(key)
    raw = data_dir("raw", key)
    years = ws.get("nlcd_years", {"impervious": [2001, 2011, 2021], "cover": [2021]})
    if not force and all((raw / f"nlcd_{k}_{y}.tif").exists() for k, ys in years.items() for y in ys):
        return sorted(raw.glob("nlcd_*.tif"))
    ds = pygeohydro.nlcd_bygeom(_aoi(key), resolution=30, years=years)[0]
    paths = []
    for var in ds.data_vars:  # e.g. impervious_2021, cover_2021
        p = raw / f"nlcd_{var}.tif"
        _save_da(ds[var], p, ws["crs"], 30, categorical=True)  # NLCD: keep native class/% values
        paths.append(p)
    return paths


def _get_retry(url: str, params: dict, tries: int = 4) -> requests.Response:
    import time

    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=300)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(5 * 2**i)


def _sda(sql: str) -> pd.DataFrame:
    r = requests.post(SDA, data={"query": sql, "format": "JSON+COLUMNNAME"}, timeout=300)
    r.raise_for_status()
    rows = r.json().get("Table", [])
    return pd.DataFrame(rows[1:], columns=rows[0]) if rows else pd.DataFrame()


def fetch_soils(key: str, force: bool = False) -> Path:
    """SSURGO map-unit polygons with dominant hydrologic soil group, one HUC-12 at a time."""
    out = data_dir("raw", key) / "ssurgo.parquet"
    if out.exists() and not force:
        return out
    hucs = gpd.read_parquet(data_dir("interim", key) / "huc12.parquet")
    parts = []
    for _, h in hucs.iterrows():
        aoi_wkt = h.geometry.simplify(0.0005).wkt
        df = _sda(f"""
            SELECT p.mupolygonkey, p.mukey, p.mupolygongeo.STAsText() AS wkt,
                   a.hydgrpdcd, a.drclassdcd, a.wtdepannmin, a.musym, a.muname
            FROM mupolygon p
            JOIN muaggatt a ON a.mukey = p.mukey
            WHERE p.mupolygonkey IN (
                SELECT * FROM SDA_Get_Mupolygonkey_from_intersection_with_WktWgs84('{aoi_wkt}'))""")
        parts.append(df)
    df = pd.concat(parts).drop_duplicates("mupolygonkey")
    gdf = gpd.GeoDataFrame(df.drop(columns="wkt"), geometry=df.wkt.map(wkt.loads), crs=4326)
    gdf.to_parquet(out)
    return out


NFHL_FIELDS = "OBJECTID,FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE,DEPTH,DFIRM_ID,SOURCE_CIT"


def _nfhl_by_ids(ids: list[int]) -> list[dict]:
    """Fetch NFHL features by OBJECTID; split the batch in half whenever the server 500s."""
    try:
        r = _get_retry(NFHL_ZONES, tries=2, params={
            "objectIds": ",".join(map(str, ids)), "outFields": NFHL_FIELDS, "outSR": 4326,
            "geometryPrecision": 7, "f": "geojson"})
        return r.json()["features"]
    except requests.RequestException:
        if len(ids) == 1:
            raise
        mid = len(ids) // 2
        return _nfhl_by_ids(ids[:mid]) + _nfhl_by_ids(ids[mid:])


def fetch_fema(key: str, force: bool = False) -> Path:
    """FEMA NFHL flood hazard zones (S_FLD_HAZ_AR) intersecting the AOI."""
    out = data_dir("raw", key) / "fema_nfhl_zones.parquet"
    if out.exists() and not force:
        return out
    w, s, e, n = _aoi(key).total_bounds
    ids = _get_retry(NFHL_ZONES, params={
        "where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "returnIdsOnly": "true",
        "f": "json"}).json()["objectIds"]
    ids = sorted(ids)
    feats = []
    for i in range(0, len(ids), 50):
        feats += _nfhl_by_ids(ids[i:i + 50])
    gdf = gpd.GeoDataFrame.from_features(feats, crs=4326)
    gdf = gdf[gdf.intersects(_aoi(key).geometry.iloc[0])]
    gdf.to_parquet(out)
    return out


def fetch_buildings(key: str, force: bool = False) -> Path:
    """Overture Maps building footprints, clipped to the buffered AOI."""
    raw = data_dir("raw", key)
    out = raw / "buildings_overture.parquet"
    if out.exists() and not force:
        return out
    tmp = raw / "_overture_bbox.parquet"
    w, s, e, n = _aoi(key).total_bounds
    subprocess.run([str(Path(sys.executable).parent / "overturemaps"), "download",
                    f"--bbox={w},{s},{e},{n}", "-f", "geoparquet", "--type=building",
                    "-o", str(tmp)], check=True)
    b = gpd.read_parquet(tmp)
    b = b[b.intersects(_aoi(key).geometry.iloc[0])]
    keep = [c for c in ["id", "height", "num_floors", "class", "subtype", "sources", "geometry"] if c in b]
    b[keep].to_parquet(out)
    tmp.unlink()
    return out


FETCHERS = {
    "dem10": lambda k, f: fetch_dem(k, 10, f),
    "dem1": lambda k, f: fetch_dem(k, 1, f),
    "nlcd": fetch_nlcd,
    "soils": fetch_soils,
    "fema": fetch_fema,
    "buildings": fetch_buildings,
}
