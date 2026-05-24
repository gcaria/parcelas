# Parcelas 🌤️

**Chile's Yearly Clear Sky Percentage** — an interactive map visualizing how often the sky is clear across Chile, built from satellite imagery.

## Live Demo

🌐 [View on GitHub Pages](https://gcaria.github.io/parcelas/)

## Overview

Parcelas processes satellite classification bands from the [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/) to compute, per pixel, the fraction of cloud-free observations over a given year. It currently supports Landsat 8/9 QA pixels and Sentinel-2 Scene Classification Layer data. The results are stored as Cloud Optimized GeoTIFFs (COGs) on Google Cloud Storage and served through a TiTiler mosaic API, rendered in a lightweight Leaflet frontend.

## Features

- Per-pixel clear sky percentage computed from satellite classification bands
- Landsat WRS-2 path/row and Sentinel-2 MGRS tile support
- COG output clipped to the requested area of interest
- Mosaic generation and validation via a FastAPI backend
- Interactive Leaflet map with a coolwarm colorbar
- API key authentication and IP-based rate limiting
- Docker-based local development

## Quickstart

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- A Google Cloud project with GCS access (for production)

### Local Development

1. **Clone the repo**

```bash
git clone https://github.com/gcaria/parcelas.git
cd parcelas
```

2. **Configure the frontend**

```bash
cp frontend/config.js.example frontend/config.js
# Edit config.js and set your API_KEY
```

3. **Start the API server**

```bash
docker compose up
```

The API will be available at `http://localhost:8080`.

4. **Open the frontend**

Serve the `frontend/` directory with any static file server, e.g.:

```bash
npx serve frontend/
```

Then navigate to `http://localhost:3001`.

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `API_KEY` | Secret key for authenticating API requests | — |
| `COG_STORAGE_URL` | GCS path to COG files (e.g. `gs://my-bucket/cogs`) | — |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:3001` |

### Running the Data Pipeline

To fetch satellite data, compute clear sky percentages, and store a COG:

For Landsat, pass a WRS-2 path and row. If no explicit clipping geometry is provided
when storing, the pipeline uses the matching WRS-2 tile boundary.

```python
from data_pipeline.clear_sky import run_clear_sky_pipeline
from data_pipeline.shapefiles import get_wrs2_tile

path, row = 233, 87
shp = get_wrs2_tile(path, row)

output_path = run_clear_sky_pipeline(
    shp=shp,
    path=path,
    row=row,
    sensor="landsat",
    time_range="2020-01-01/2020-12-31",
    output_template="gs://my-bucket/cogs/{tile_key}_uint8.tif",
)
```

For Sentinel-2, pass an MGRS tile ID and the area of interest to process:

```python
import geopandas as gpd
from shapely.geometry import box

from data_pipeline.clear_sky import run_clear_sky_pipeline

shp = gpd.GeoDataFrame(
    geometry=[box(-70.9, -33.7, -70.4, -33.3)],
    crs="EPSG:4326",
)

output_path = run_clear_sky_pipeline(
    shp=shp,
    tile_id="T19HCD",
    sensor="sentinel2",
    time_range="2020-01-01/2020-12-31",
    output_template="gs://my-bucket/cogs/{tile_key}_uint8.tif",
)
```

The `{tile_key}` placeholder standardizes output names, for example
`landsat_233_087_uint8.tif` and `sentinel2_19HCD_uint8.tif`.

### Generating a Mosaic

Once COGs are on GCS, generate a mosaic JSON via the API:

```bash
curl -X POST "http://localhost:8080/mosaicjson/generate?sensor=landsat&save_to_gcs=true&glob_pattern=uint8" \
  -H "X-API-Key: <api-key>"

curl -X POST "http://localhost:8080/mosaicjson/generate?sensor=sentinel2&save_to_gcs=true&glob_pattern=uint8" \
  -H "X-API-Key: <api-key>"
```

## API Reference

All endpoints (except `/health`) require an `X-API-Key` header or `api_key` query parameter. Rate limit: 100 requests per 60 seconds per IP.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check (public) |
| `POST` | `/mosaicjson/generate` | Generate and optionally save a mosaic JSON from COGs |
| `GET` | `/mosaicjson/validate` | Validate an existing mosaic JSON on GCS |
| `GET` | `/mosaicjson/tiles/{z}/{x}/{y}.png` | Serve map tiles from a mosaic |

### Tile URL Example

```
/mosaicjson/tiles/WebMercatorQuad/{z}/{x}/{y}.png
  ?url=gs://my-bucket/mosaics/mosaic_uint8.json.gz
  &rescale=0,100
  &colormap_name=coolwarm
  &clamp=true
  &api_key=YOUR_API_KEY
```

## Running Tests

```bash
pytest tests/
```

## Tech Stack

- **Data**: Landsat 8/9 and Sentinel-2 via [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/) · `odc-stac` · `rioxarray`
- **Backend**: FastAPI · [TiTiler](https://developmentseed.org/titiler/) · `cogeo-mosaic` · `gcsfs`
- **Frontend**: Leaflet.js
- **Infrastructure**: Google Cloud Storage · Google Cloud Run · Docker

## License

MIT
