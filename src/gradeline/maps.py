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


def boundary_png(key: str, hucs: gpd.GeoDataFrame, points: dict[str, tuple[float, float]] | None = None) -> str:
    """Static map for review when an interactive map isn't handy. points: label -> (lat, lon)."""
    import contextily as cx
    import matplotlib.pyplot as plt

    h = hucs.to_crs(3857)
    fig, ax = plt.subplots(figsize=(13, 8), dpi=130)
    h.boundary.plot(ax=ax, color="#60a5fa", lw=1)
    h.dissolve().boundary.plot(ax=ax, color="#dc2626", lw=2.5)
    for _, r in h.iterrows():
        p = r.geometry.representative_point()
        ax.annotate(f"…{r.huc12[-2:]}\n{r['name']}", (p.x, p.y), ha="center", fontsize=7,
                    color="white", weight="bold")
    if points:
        pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy([v[1] for v in points.values()],
                               [v[0] for v in points.values()]), crs=4326).to_crs(3857)
        pts.plot(ax=ax, color="#facc15", edgecolor="black", markersize=40, zorder=5)
        for label, g in zip(points, pts.geometry):
            ax.annotate(label, (g.x, g.y), xytext=(5, 5), textcoords="offset points",
                        fontsize=7, color="#facc15", weight="bold")
    cx.add_basemap(ax, source=cx.providers.Esri.WorldImagery, zoom=12, attribution_size=5)
    ax.set_axis_off()
    ax.set_title(f"{key}: WBD HUC-12s (blue), AOI (red), USGS gauges (yellow)")
    path = MAPS / f"{key}_boundary.png"
    fig.savefig(path, bbox_inches="tight")
    return str(path)
