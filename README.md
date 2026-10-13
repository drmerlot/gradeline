# Gradeline

Building-level water risk from free public data.

- **Engine**: terrain, soils, land cover, rainfall, FEMA → per-building features, by HUC watershed
- **Product 1**: `GET /water-risk?address=...` + MCP server
- **Product 2**: draft HEC-HMS / SWMM models

## Setup
```bash
uv sync
```

## Pipeline (Onion Creek)
```bash
uv run gradeline boundary onion_creek          # WBD HUC-12s -> AOI + preview map
uv run gradeline fetch onion_creek             # DEM 10 m + 1 m pilot, NLCD, SSURGO, FEMA, buildings
uv run gradeline terrain onion_creek --res 10  # fill, routing, HAND (multi-scale), upstream impervious
uv run gradeline terrain onion_creek --res 1   # 1 m pilot (micro-topography)
uv run gradeline validate onion_creek          # routing vs USGS gauge drainage areas
uv run gradeline features onion_creek          # per-building features
uv run gradeline score onion_creek             # 0-100 score, drivers (config/scoring.yaml)
```

## API (local)
```bash
uv run uvicorn gradeline.api:app --port 8000
curl -H "X-API-Key: dev-local" "localhost:8000/water-risk?address=6301+Bluff+Springs+Rd,+Austin,+TX+78744"
```
Keys: `config/api_keys.yaml` (gitignored) `{key: {name, daily_cap}}`; usage logged to `data/usage.sqlite`.

## Layout
- `config/watersheds.yaml` — one entry per watershed
- `src/gradeline/` — pipeline code
- `data/{raw,interim,processed}/<watershed>/` — not in git
- `maps/` — HTML previews

Outputs are drafts for engineer review.
