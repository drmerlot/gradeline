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


def risk_png(key: str) -> str:
    """Left: whole watershed, homes outside FEMA flagged risky. Right: 1 m pilot with probable buyouts."""
    import contextily as cx
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from shapely.geometry import box

    from .config import data_dir, load_watershed

    ws = load_watershed(key)
    s = gpd.read_parquet(data_dir("processed", key) / "buildings_scored.parquet").to_crs(3857)
    aoi = gpd.read_parquet(data_dir("interim", key) / "aoi.parquet").to_crs(3857)
    fema = gpd.read_parquet(data_dir("raw", key) / "fema_nfhl_zones.parquet")
    sfha = fema[fema.SFHA_TF == "T"].to_crs(3857)
    gone = gpd.read_parquet(data_dir("processed", key) / "backtest_probable_buyouts.parquet").to_crs(3857)
    pilot = gpd.GeoSeries([box(*ws["pilot_1m_bbox"])], crs=4326).to_crs(3857)

    fig, axes = plt.subplots(1, 2, figsize=(18, 8.5), dpi=130, gridspec_kw={"width_ratios": [1.6, 1]})
    ax = axes[0]
    sfha.plot(ax=ax, color="#3b82f6", alpha=0.45, lw=0)
    risky = s[s.outside_fema_but_risky]
    risky.representative_point().plot(ax=ax, color="#ef4444", markersize=4)
    aoi.boundary.plot(ax=ax, color="white", lw=1.5)
    pilot.boundary.plot(ax=ax, color="#facc15", lw=1.5, ls="--")
    cx.add_basemap(ax, source=cx.providers.Esri.WorldImagery, zoom=12, attribution_size=5)
    ax.set_axis_off()
    ax.set_title(f"Onion Creek: {len(risky):,} buildings outside FEMA 100-yr floodplain with score ≥ 40 (red)\n"
                 "blue = FEMA 100-yr floodplain, yellow dashed = 1 m lidar pilot", fontsize=10)

    ax = axes[1]
    sub = s[s.terrain_res == 1]
    sfha.clip(pilot.iloc[0]).plot(ax=ax, color="#3b82f6", alpha=0.35, lw=0)
    gpd.GeoDataFrame({"score": sub.score.clip(0, 70)}, geometry=sub.representative_point()).plot(
        ax=ax, column="score", cmap="YlOrRd", markersize=2, vmin=0, vmax=70)
    gone.representative_point().plot(ax=ax, color="black", marker="x", markersize=10, lw=0.8)
    ax.set_xlim(*pilot.total_bounds[[0, 2]])
    ax.set_ylim(*pilot.total_bounds[[1, 3]])
    cx.add_basemap(ax, source=cx.providers.Esri.WorldGrayCanvas, zoom=14, attribution_size=5)
    ax.set_axis_off()
    pct = lambda d: 100 * (d.score >= 40).mean()  # noqa: E731
    ax.set_title(f"Dove Springs / lower Onion (1 m): score (yellow→red), probable buyouts (x)\n"
                 f"≥40: {pct(gone):.0f}% of {len(gone)} probable buyouts vs {pct(sub):.0f}% of homes still standing",
                 fontsize=10)
    ax.legend(handles=[Line2D([], [], color="black", marker="x", ls="", label="gone since 2013 (probable buyout)")],
              loc="lower right", fontsize=8)
    path = MAPS / f"{key}_risk.png"
    fig.savefig(path, bbox_inches="tight")
    return str(path)
