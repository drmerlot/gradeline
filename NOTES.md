# Notes: decisions and known issues

## Decisions
- 2026-10-13: Onion Creek AOI = full HUC-10 1209020504 = HUC-12s 0401–0409 (944 km²). 0409 must be included: lower Onion Ck (USGS 08159000 at US-183, 321 mi²) and the buyout areas fall there, not in 0408. 0409 also holds Carson Ck + Colorado mainstem area that does not drain to Onion; delineate Onion-only drainage from the DEM later (for HMS).
- 2026-10-13: rasterio pinned <1.5 (1.5.2 has no macOS 14 wheel).
- 2026-10-13: 1 m DEM via windowed reads of USGS 1 m COG tiles (TNM API), not py3dep WMS (timed out after 1.5 GB cache). Pilot = bbox around Dove Springs/lower Onion (~65 km2, TX Central B1 2017 lidar).
- 2026-10-13: Terrain = pysheds + own numba priority-flood/epsilon fill. Whitebox Workflows 2.0.6 dropped (5 min/step on full grid, NaN nodata hang); pysheds resolve_flats left ~850 dead ends. 10 m routing matches 7 USGS gauges within ±1%.
- 2026-10-13: Riverine risk = HAND / expected 100-yr depth, depth = 1.21*A_km2^0.325 fitted to HAND at FEMA AE edges. HAND at 1, 10, 100 km2 stream networks, worst ratio wins (backwater). HAND always from 10 m; 1 m used only for depression depth.
- 2026-10-13: Backtest: probable buyouts = CoA 2013 footprints missing from Overture (708, 70% in SFHA). Score>=40: 50% of them vs 4.5% of standing homes in same area.

## Known issues and next steps
- No depression breaching: 1 m depression depth includes road-embankment "digital dams"
- ~96% of outside-FEMA flags come from small (~1-3 km2) DEM streams; in suburbs many are streets acting as drainage paths -> likely overstated. Validate (City of Austin local flood complaints / 311?) and consider storm-sewer-aware streams
- Address->building match uses Census interpolated points (often ~40 m off); add TxGIO parcels
- Features step ~7 min (FEMA sjoin_nearest 150 s); optimize later
- City of Austin Atlas-14 floodplains not yet compared
