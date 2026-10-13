# Gradeline

Building-level water risk from free public data.

- **Engine**: terrain, soils, land cover, rainfall, FEMA → per-building features, by HUC watershed
- **Product 1**: `GET /water-risk?address=...` + MCP server
- **Product 2**: draft HEC-HMS / SWMM models

## Setup
```bash
uv sync
```

## Usage
```bash
uv run gradeline boundary onion_creek   # fetch WBD boundary + preview map
```

## Layout
- `config/watersheds.yaml` — one entry per watershed
- `src/gradeline/` — pipeline code
- `data/{raw,interim,processed}/<watershed>/` — not in git
- `maps/` — HTML previews

Outputs are drafts for engineer review.
