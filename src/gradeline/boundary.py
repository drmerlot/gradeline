"""Fetch watershed boundaries from the USGS Watershed Boundary Dataset (WBD)."""
import geopandas as gpd
import requests

from .config import data_dir, load_watershed

WBD_HUC12 = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/6/query"


def fetch_huc12(codes: list[str]) -> gpd.GeoDataFrame:
    where = "huc12 IN ({})".format(",".join(f"'{c}'" for c in codes))
    r = requests.get(
        WBD_HUC12,
        params={"where": where, "outFields": "huc12,name,areasqkm,tohuc",
                "outSR": 4326, "f": "geojson"},
        timeout=60,
    )
    r.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(r.json()["features"], crs=4326)
    missing = set(codes) - set(gdf["huc12"])
    if missing:
        raise ValueError(f"WBD returned no geometry for HUC-12s: {sorted(missing)}")
    return gdf.sort_values("huc12").reset_index(drop=True)


def build_boundary(key: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Save HUC-12 polygons, the dissolved AOI, and a buffered AOI for terrain work."""
    ws = load_watershed(key)
    hucs = fetch_huc12(ws["huc12"])
    aoi = hucs.dissolve()[["geometry"]].assign(watershed=key)
    aoi_buf = aoi.to_crs(ws["crs"]).buffer(ws.get("buffer_m", 0)).to_crs(4326)

    out = data_dir("interim", key)
    hucs.to_parquet(out / "huc12.parquet")
    aoi.to_parquet(out / "aoi.parquet")
    gpd.GeoDataFrame(geometry=aoi_buf).to_parquet(out / "aoi_buffered.parquet")
    return hucs, aoi
