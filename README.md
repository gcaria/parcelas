# parcelas

Estimates the percentage of yearly sunny days across Chile by processing Landsat 8 & 9 satellite imagery.

## Stack

- **Processing** — Python (xarray) for Landsat 8/9 cloud/shadow classification and sunny-day aggregation
- **Backend** — FastAPI + TiTiler for serving and visualizing computed raster tiles
- **Frontend** — Leaflet for interactive map exploration

## Data

Landsat 8 & 9 scenes are sourced from [USGS Earth Explorer](https://earthexplorer.usgs.gov/) or [Google Earth Engine](https://developers.google.com/earth-engine/datasets/catalog/LANDSAT_LE07_C02_T1_SR). Scenes are filtered to the Chilean extent and processed offline before being served as COGs via TiTiler.
