import folium
import geopandas as gpd

from .config import MAPS


def boundary_map(key: str, hucs: gpd.GeoDataFrame, aoi: gpd.GeoDataFrame) -> str:
    c = aoi.to_crs(26914).centroid.to_crs(4326).iloc[0]
    m = folium.Map(location=[c.y, c.x], zoom_start=10, tiles="OpenStreetMap")
    folium.TileLayer("Esri.WorldImagery", name="Imagery").add_to(m)
    folium.GeoJson(
        hucs, name="HUC-12s",
        style_function=lambda _: {"color": "#2563eb", "weight": 1.5, "fillOpacity": 0.08},
        tooltip=folium.GeoJsonTooltip(["huc12", "name", "areasqkm"]),
    ).add_to(m)
    folium.GeoJson(
        aoi, name="AOI",
        style_function=lambda _: {"color": "#dc2626", "weight": 3, "fillOpacity": 0},
    ).add_to(m)
    folium.LayerControl().add_to(m)
    MAPS.mkdir(exist_ok=True)
    path = MAPS / f"{key}_boundary.html"
    m.save(path)
    return str(path)
