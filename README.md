# Parcelas 🌤️

**Chile's Yearly Clear Sky Percentage** — an interactive map visualizing how often the sky is clear across Chile, built from satellite imagery.

## Live Demo

🌐 [View on GitHub Pages](https://gcaria.github.io/parcelas/)

## Overview

Parcelas processes satellite classification bands from the [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/) to compute, per pixel, the fraction of valid observations that are cloud-free. It supports Landsat 8/9 QA pixels and Sentinel-2 Scene Classification Layer (SCL) data. Results are stored as Cloud Optimized GeoTIFFs (COGs) on Google Cloud Storage, served through a TiTiler mosaic API, and rendered in a lightweight Leaflet frontend.

## Features

- Per-pixel clear sky percentage computed from satellite classification bands
- Landsat WRS-2 path/row and Sentinel-2 MGRS tile support
- COG output clipped to the requested area of interest
- Valid-observation denominators that exclude nodata and masked water pixels
- Sequential, observation-weighted multi-year Sentinel-2 processing
- Progressive Sentinel-2 mosaics assembled from completed tile workflows
- Mosaic generation and validation via a FastAPI backend
- Interactive Leaflet map with Landsat/Sentinel selection, data visibility, and street/satellite basemap controls
- A responsive discrete 0–100% clear-sky colorbar
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
| `API_KEY` | Server-side secret for administrative API requests | — |
| `COG_STORAGE_URL` | GCS path to COG files (e.g. `gs://my-bucket/cogs`) | — |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:3001` |

### Running the Data Pipeline

To fetch satellite data, compute clear sky percentages, and store a COG:

For Landsat, pass a WRS-2 path and row. The pipeline uses the matching WRS-2
tile boundary when `aoi_geojson` is omitted.

```python
from data_pipeline.clear_sky import run_clear_sky_pipeline

output_path = run_clear_sky_pipeline(
    path=233,
    row=87,
    sensor="landsat",
    time_range="2020-01-01/2020-12-31",
    output_template="gs://my-bucket/cogs/{tile_key}_uint8.tif",
)
```

For Sentinel-2, pass an MGRS tile ID. The MGRS tile footprint is used when
`aoi_geojson` is omitted:

```python
from data_pipeline.clear_sky import run_clear_sky_pipeline

output_path = run_clear_sky_pipeline(
    tile_id="T19HCD",
    sensor="sentinel2",
    time_range="2020-01-01/2020-12-31",
    output_template="gs://my-bucket/cogs/{tile_key}_uint8.tif",
)
```

The `{tile_key}` placeholder standardizes output names, for example
`landsat_233_087_uint8.tif` and `sentinel2_19HCD_uint8.tif`.

The equivalent command-line entry point is:

```bash
python -m data_pipeline.run_tile \
  --sensor sentinel2 \
  --tile-id T19HCD \
  --time-range 2020-01-01/2020-12-31 \
  --output-template 'output/{tile_key}_uint8.tif'
```

### Running a Tile on GitHub Actions

The **Run tile pipeline** workflow can process a tile without a local setup. In
the repository's Actions tab, select the workflow, choose **Run workflow**, and
provide either a Sentinel-2 tile ID or a Landsat path and row. The generated COG
is available from the completed run as an artifact for 14 days.

To create a clickable map preview, add a `GCP_SERVICE_ACCOUNT_KEY` repository
secret containing credentials that can write to the preview bucket, then enable
`publish_preview` when starting the workflow. The completed run summary links to
the frontend with its temporary one-tile mosaic selected. Configure a lifecycle
rule on the bucket's `previews/` prefix to remove old preview files.

### Running Multiple Years Sequentially

The **Run multi-year Sentinel pipeline** workflow processes complete calendar
years one at a time to avoid retaining the full temporal stack in memory. Each
year is reduced to two count bands:

- `clear_count`: clear observations per pixel
- `valid_count`: valid observations per pixel

The count bands are added immediately to a single windowed `uint32` accumulator
on disk, rather than retaining annual rasters. The final percentage is calculated
as `100 × sum(clear_count) / sum(valid_count)`. Annual percentages are not
averaged because observation counts vary by year and pixel. The static JRC water
mask is fetched and reprojected once after the merge, and only the final output is
encoded as a compressed COG. Comparison outputs are published separately from
the one-year regional mosaic.

When a GCS checkpoint prefix is configured, the workflow uploads the count
accumulator after every completed year and records the latest completed year in
a manifest. A rerun restores that accumulator and continues with the next year.
Planetary Computer signing tokens are refreshed before every annual search so a
long-running job does not reuse a token near expiry. Checkpoints are compatible
only with the same tile, year range, and clipping buffer.

The same operation can be run from the command line:

```bash
python -m data_pipeline.run_multiyear \
  --tile-id T19HCD \
  --start-year 2020 \
  --end-year 2024 \
  --output output/sentinel2_19HCD_2020_2024_uint8.tif \
  --checkpoint-prefix gs://my-bucket/checkpoints/sentinel2_19HCD_2020_2024
```

### Progressive Sentinel Preview

The **Update progressive Sentinel preview** workflow runs every five minutes. It
discovers successful Sentinel preview COGs, keeps the greatest GitHub run ID for
each MGRS tile, and republishes the shared mosaic at:

```text
gs://parcelas-wrs2/previews/progressive/sentinel2-2020.json.gz
```

The deployed frontend uses this shared mosaic for its Sentinel-2 layer. The
deployed Landsat layer uses
`gs://parcelas-wrs2/mosaics/mosaic_masked.json.gz`. A one-off preview can be
opened without changing frontend configuration by passing `sensor` and `mosaic`
query parameters:

```text
https://gcaria.github.io/parcelas/?sensor=sentinel2&mosaic=gs://bucket/path/mosaic.json.gz
```

### Generating a Mosaic

Once COGs are on GCS, generate a mosaic JSON via the API:

```bash
curl -X POST "http://localhost:8080/mosaicjson/generate?sensor=landsat&save_to_gcs=true&glob_pattern=uint8" \
  -H "X-API-Key: <api-key>"

curl -X POST "http://localhost:8080/mosaicjson/generate?sensor=sentinel2&save_to_gcs=true&glob_pattern=uint8" \
  -H "X-API-Key: <api-key>"
```

## API Reference

The read-only `/health`, `/mosaicjson/sensors`, `/mosaicjson/info`, and tile
routes are public so the static frontend does not contain an administrative
secret. Mosaic generation and validation require an `X-API-Key` header or
`api_key` query parameter. Non-tile API operations are limited to 100 requests
per 60 seconds per client. Tile reads are exempt from the in-process IP limiter
because Cloud Run does not reliably expose distinct client addresses to the
application.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check (public) |
| `GET` | `/mosaicjson/sensors` | List configured sensor mosaics (public) |
| `POST` | `/mosaicjson/generate` | Generate and optionally save a mosaic JSON from COGs |
| `GET` | `/mosaicjson/validate` | Validate an existing mosaic JSON on GCS |
| `GET` | `/mosaicjson/info` | Return mosaic bounds and zoom metadata |
| `GET` | `/mosaicjson/tiles/WebMercatorQuad/{z}/{x}/{y}.png` | Serve map tiles from a mosaic |

### Tile URL Example

```text
/mosaicjson/tiles/WebMercatorQuad/{z}/{x}/{y}.png
  ?url=gs://my-bucket/mosaics/mosaic_uint8.json.gz
  &rescale=0,100
  &colormap_name=coolwarm
  &clamp=true
```

The frontend uses a ten-class discrete color table after rescaling values to
0–100. Direct API requests can use a named colormap as shown above or supply a
custom `colormap` lookup table.

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
