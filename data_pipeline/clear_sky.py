"""Fetch satellite data and compute clear-sky percentages."""

import logging
from typing import Any, List, Literal

import geopandas
import odc.stac
import planetary_computer
import pystac_client
import rioxarray  # noqa: F401
import shapely
import xarray

from data_pipeline.shapefiles import get_mgrs_tile, get_wrs2_tile

LANDSAT_CLEAR_SKY_QA_FLAGS = [
    21824,  # clear with lows set
    21826,  # dilated cloud over land
    21888,  # water with lows set
    21890,  # dilated cloud over water
    30048,  # high conf snow/ice
    54596,  # high conf cirrus
]
SENTINEL2_CLEAR_SKY_SCL_CLASSES = [
    4,  # vegetation
    5,  # not vegetated
    6,  # water
    11,  # snow/ice
]
CLEAR_SKY_QA_FLAGS = LANDSAT_CLEAR_SKY_QA_FLAGS
PLANETARY_COMPUTER_CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
Sensor = Literal["landsat", "sentinel2"]

SENSOR_CONFIGS: dict[Sensor, dict[str, Any]] = {
    "landsat": {
        "collection": "landsat-c2-l2",
        "default_bands": ["qa_pixel"],
        "data_band": "qa_pixel",
        "clear_sky_flags": LANDSAT_CLEAR_SKY_QA_FLAGS,
        "nodata": 65535,
        "display_name": "Landsat",
    },
    "sentinel2": {
        "collection": "sentinel-2-l2a",
        "default_bands": ["SCL"],
        "data_band": "SCL",
        "clear_sky_flags": SENTINEL2_CLEAR_SKY_SCL_CLASSES,
        "nodata": 0,
        "display_name": "Sentinel-2",
    },
}


def get_satellite_data(
    shp: "geopandas.GeoDataFrame",
    path: int | None = None,
    row: int | None = None,
    tile_id: str | None = None,
    sensor: Sensor = "landsat",
    time_range: str = "2020-01-01/2020-12-31",
    bands: List[str] | None = None,
    chunks: dict = {"x": 512, "y": 512},
    mask_water: bool = True,
) -> "xarray.DataArray":
    """
    Fetch satellite data from the Microsoft Planetary Computer.

    Args:
        shp: A GeoDataFrame containing the geometry of the area of interest.
        path: The WRS-2 path number. Required for Landsat.
        row: The WRS-2 row number. Required for Landsat.
        tile_id: The Sentinel-2 MGRS tile ID, such as "19HCD" or "T19HCD".
            Optional for Sentinel-2.
        sensor: The satellite sensor to fetch. Supported values are "landsat" and
            "sentinel2".
        time_range: The time range for which to fetch data, in the format "YYYY-MM-DD/YYYY-MM-DD".
        bands: A list of band names to fetch.
        chunks: A dictionary specifying the chunk sizes for the xarray Dataset.
        mask_water: A boolean indicating whether to mask out water pixels based on the JRC Global Surface Water dataset.

    Returns:
        An xarray DataArray containing the requested classification band.

    Raises:
        ValueError: If the sensor is unsupported, or if Landsat is requested without
            a path and row.
    """
    if sensor not in SENSOR_CONFIGS:
        supported_sensors = ", ".join(SENSOR_CONFIGS)
        raise ValueError(
            f"Unsupported sensor '{sensor}'. Use one of: {supported_sensors}"
        )

    if sensor == "landsat" and (path is None or row is None):
        raise ValueError("path and row are required when sensor='landsat'")

    config = SENSOR_CONFIGS[sensor]
    bands = bands or config["default_bands"]
    data_band = config["data_band"]
    normalized_tile_id = (
        _normalize_sentinel2_tile_id(tile_id)
        if sensor == "sentinel2" and tile_id is not None
        else None
    )

    catalog = pystac_client.Client.open(
        PLANETARY_COMPUTER_CATALOG_URL,
        modifier=planetary_computer.sign_inplace,
    )

    query = None
    if sensor == "landsat":
        query = {
            "landsat:wrs_path": {"eq": f"{path:03d}"},
            "landsat:wrs_row": {"eq": f"{row:03d}"},
            "platform": {"in": ["landsat-8", "landsat-9"]},
        }
    elif normalized_tile_id is not None:
        query = {"s2:mgrs_tile": {"eq": normalized_tile_id}}

    search = catalog.search(
        collections=[config["collection"]],
        intersects=shp.union_all(),
        datetime=time_range,
        query=query,
    )

    items = search.item_collection()
    tile_message = _format_tile_message(path=path, row=row, tile_id=normalized_tile_id)
    logging.info(
        f"Found {len(items)} {config['display_name']} items{tile_message} in time range {time_range}"
    )

    da_sat = odc.stac.stac_load(
        items,
        bands=bands,
        intersects=shp.union_all(),
        chunks=chunks,
        nodata=config["nodata"],
    )[data_band]

    if mask_water:
        da_sw = get_jrc_surface_water(shp, chunks=chunks)["occurrence"]
        da_sw = da_sw.rio.reproject_match(da_sat).squeeze()
        da_sat = da_sat.where(da_sw < 90)

    da_sat.attrs["sensor"] = sensor
    da_sat.attrs["clear_sky_flags"] = config["clear_sky_flags"]
    da_sat.attrs["aoi_wkt"] = shp.union_all().wkt
    da_sat.attrs["aoi_crs"] = str(shp.crs)

    return da_sat


def _format_tile_message(path: int | None, row: int | None, tile_id: str | None) -> str:
    """Format optional tile details for log messages."""
    if path is not None and row is not None:
        return f" for path {path}/ row {row}"

    if tile_id is not None:
        return f" for tile {tile_id}"

    return ""


def format_satellite_tile_key(
    sensor: Sensor,
    path: int | None = None,
    row: int | None = None,
    tile_id: str | None = None,
) -> str:
    """
    Format a stable output tile key for a supported satellite sensor.

    Args:
        sensor: The satellite sensor. Supported values are "landsat" and "sentinel2".
        path: The WRS-2 path number. Required for Landsat.
        row: The WRS-2 row number. Required for Landsat.
        tile_id: The Sentinel-2 MGRS tile ID. Required for Sentinel-2.

    Returns:
        A stable tile key suitable for file names.

    Raises:
        ValueError: If required tile identifiers are missing or the sensor is unsupported.
    """
    if sensor == "landsat":
        if path is None or row is None:
            raise ValueError("path and row are required when sensor='landsat'")
        return f"landsat_{path:03d}_{row:03d}"

    if sensor == "sentinel2":
        if tile_id is None:
            raise ValueError("tile_id is required when sensor='sentinel2'")
        return f"sentinel2_{_normalize_sentinel2_tile_id(tile_id)}"

    supported_sensors = ", ".join(SENSOR_CONFIGS)
    raise ValueError(f"Unsupported sensor '{sensor}'. Use one of: {supported_sensors}")


def _normalize_sentinel2_tile_id(tile_id: str) -> str:
    """Normalize Sentinel-2 tile IDs to the MGRS value used by STAC."""
    normalized = tile_id.strip().upper()
    if normalized.startswith("T"):
        normalized = normalized[1:]

    if not normalized:
        raise ValueError("tile_id must not be empty")

    return normalized


def _make_clip_geometry(
    clip_shp: "geopandas.GeoDataFrame",
    raster_crs: Any,
    buffer: int,
):
    """Create a clipping geometry in raster CRS, buffering in meters."""
    clip_shp = clip_shp.to_crs(raster_crs)

    if clip_shp.crs and clip_shp.crs.is_geographic:
        metric_crs = clip_shp.estimate_utm_crs()
        if metric_crs is None:
            raise ValueError("Unable to estimate a projected CRS for clipping")

        metric_shp = clip_shp.to_crs(metric_crs)
        poly = metric_shp.union_all().simplify(tolerance=1000).buffer(buffer)
        return geopandas.GeoSeries([poly], crs=metric_crs).to_crs(raster_crs).iloc[0]

    return clip_shp.union_all().simplify(tolerance=1000).buffer(buffer)


def get_landsat_data(
    shp: "geopandas.GeoDataFrame",
    path: int,
    row: int,
    time_range: str = "2020-01-01/2020-12-31",
    bands: List[str] | None = None,
    chunks: dict = {"x": 512, "y": 512},
    mask_water: bool = True,
) -> "xarray.DataArray":
    """
    Fetch Landsat 8 and 9 data from the Microsoft Planetary Computer.

    This wrapper is kept for backward compatibility. Prefer get_satellite_data().
    """
    return get_satellite_data(
        shp=shp,
        path=path,
        row=row,
        sensor="landsat",
        time_range=time_range,
        bands=bands,
        chunks=chunks,
        mask_water=mask_water,
    )


def compute_clear_sky_percentage(
    da_ls: "xarray.DataArray", clear_sky_qa_flags: List[int] | None = None
) -> "xarray.DataArray":
    """
    Compute the percentage of clear-sky pixels in a satellite classification band.

    Args:
        da_ls: An xarray DataArray containing a satellite classification band.
        clear_sky_qa_flags: Classification values that indicate clear sky conditions.
            Defaults to values stored by get_satellite_data(), or Landsat QA flags.

    Returns:
        An xarray DataArray containing the percentage of clear sky pixels for each spatial location.

    Raises:
        ValueError: If the input DataArray has no time observations.
    """
    if len(da_ls.time) == 0:
        raise ValueError("Cannot compute clear sky percentage from empty data")

    if clear_sky_qa_flags is None:
        clear_sky_qa_flags = da_ls.attrs.get("clear_sky_flags", CLEAR_SKY_QA_FLAGS)
    clear_sky = da_ls.isin(clear_sky_qa_flags)
    _sum = clear_sky.astype(int).sum(dim="time")

    return _sum / len(da_ls.time)


def store_clear_sky_percentage(
    da_csp: "xarray.DataArray",
    path: int | None = None,
    row: int | None = None,
    tile_id: str | None = None,
    sensor: Sensor = "landsat",
    output_template: str = "{tile_key}.tif",
    buffer: int = -500,
) -> str:
    """
    Store clear sky percentage data as a Cloud Optimized GeoTIFF (COG) file.

    The clipping geometry is read from the ``"aoi_wkt"`` and ``"aoi_crs"``
    attributes attached to ``da_csp`` by :func:`get_satellite_data`. The same
    AOI that was used to fetch the data is therefore always used to clip the
    output, shrunk inward by ``buffer`` metres.

    Args:
        da_csp: An xarray DataArray containing the percentage of clear sky pixels
            for each spatial location. Must carry ``aoi_wkt`` and ``aoi_crs``
            attributes set by :func:`get_satellite_data`.
        path: The WRS-2 path number. Required for Landsat.
        row: The WRS-2 row number. Required for Landsat.
        tile_id: The Sentinel-2 MGRS tile ID. Required for Sentinel-2.
        sensor: The satellite sensor. Supported values are "landsat" and "sentinel2".
        output_template: A template string for the output file name. Supports
            placeholders for tile_key, sensor, path, row, and tile_id.
        buffer: The distance in metres to buffer the clipping geometry inward.

    Returns:
        The output file name or path.
    """
    tile_key = format_satellite_tile_key(
        sensor=sensor,
        path=path,
        row=row,
        tile_id=tile_id,
    )
    aoi_geom = shapely.from_wkt(da_csp.attrs["aoi_wkt"])
    clip_shp = geopandas.GeoDataFrame(geometry=[aoi_geom], crs=da_csp.attrs["aoi_crs"])
    da_csp = (da_csp.where(da_csp > 0) * 100).fillna(0)
    da_csp = da_csp.astype("uint8").rio.write_nodata(0)

    poly = _make_clip_geometry(clip_shp, da_csp.rio.crs, buffer)

    da_csp = da_csp.rio.clip([poly], da_csp.rio.crs, drop=True)

    fname = output_template.format(
        tile_key=tile_key,
        sensor=sensor,
        path=path,
        row=row,
        tile_id=_normalize_sentinel2_tile_id(tile_id)
        if sensor == "sentinel2" and tile_id is not None
        else tile_id,
    )
    da_csp.rio.to_raster(fname, driver="COG")

    logging.info(f"Clear sky percentage stored at {fname}")
    return fname


def _load_aoi(
    sensor: Sensor,
    path: int | None,
    row: int | None,
    tile_id: str | None,
    aoi_geojson: str | None,
) -> "geopandas.GeoDataFrame":
    """
    Load the area of interest for a pipeline run.

    Resolution order:
    1. An explicit ``aoi_geojson`` path (read by GeoPandas, supports local files
       and cloud URIs such as ``gs://``).
    2. The MGRS tile footprint for Sentinel-2.
    3. The WRS-2 tile footprint for Landsat.
    """
    if aoi_geojson is not None:
        return geopandas.read_file(aoi_geojson)
    if sensor == "sentinel2":
        if tile_id is None:
            raise ValueError("tile_id is required when sensor='sentinel2'")
        return get_mgrs_tile(tile_id)
    if path is None or row is None:
        raise ValueError("path and row are required when sensor='landsat'")
    return get_wrs2_tile(path, row)


def run_clear_sky_pipeline(
    path: int | None = None,
    row: int | None = None,
    tile_id: str | None = None,
    sensor: Sensor = "landsat",
    aoi_geojson: str | None = None,
    time_range: str = "2020-01-01/2020-12-31",
    bands: List[str] | None = None,
    chunks: dict = {"x": 512, "y": 512},
    mask_water: bool = True,
    output_template: str = "{tile_key}.tif",
    buffer: int = -500,
) -> str:
    """
    Fetch satellite data, compute clear sky percentage, and store it as a COG.

    Args:
        path: The WRS-2 path number. Required for Landsat.
        row: The WRS-2 row number. Required for Landsat.
        tile_id: The Sentinel-2 MGRS tile ID. Required for Sentinel-2.
        sensor: The satellite sensor. Supported values are "landsat" and "sentinel2".
        aoi_geojson: Optional path to a GeoJSON file (local or cloud URI) to use as
            the area of interest. When omitted, the tile footprint is used (MGRS for
            Sentinel-2, WRS-2 for Landsat).
        time_range: The time range for which to fetch data, in the format "YYYY-MM-DD/YYYY-MM-DD".
        bands: A list of band names to fetch.
        chunks: A dictionary specifying chunk sizes for xarray.
        mask_water: Whether to mask out water pixels based on JRC Global Surface Water.
        output_template: A template string for the output file name. Supports
            placeholders for tile_key, sensor, path, row, and tile_id.
        buffer: The distance in meters to buffer the clipping geometry.

    Returns:
        The output file name or path.
    """
    shp = _load_aoi(
        sensor=sensor,
        path=path,
        row=row,
        tile_id=tile_id,
        aoi_geojson=aoi_geojson,
    )
    da_sat = get_satellite_data(
        shp=shp,
        path=path,
        row=row,
        tile_id=tile_id,
        sensor=sensor,
        time_range=time_range,
        bands=bands,
        chunks=chunks,
        mask_water=mask_water,
    )
    da_csp = compute_clear_sky_percentage(da_sat)
    return store_clear_sky_percentage(
        da_csp=da_csp,
        path=path,
        row=row,
        tile_id=tile_id,
        sensor=sensor,
        output_template=output_template,
        buffer=buffer,
    )


def get_jrc_surface_water(
    shp: "geopandas.GeoDataFrame",
    bands: List[str] = ["occurrence"],
    chunks: dict = {"x": 512, "y": 512},
) -> "xarray.Dataset":
    """
    Fetches JRC Global Surface Water data from the Microsoft Planetary Computer for a
    specified area of interest, and returns it as an xarray Dataset.

    Args:
        shp: A GeoDataFrame containing the geometry of the area of interest.
        bands: A list of band names to fetch.
        chunks: A dictionary specifying the chunk sizes for the xarray Dataset.

    Returns:
        An xarray Dataset containing the requested JRC Global Surface Water data.
    """
    catalog = pystac_client.Client.open(
        PLANETARY_COMPUTER_CATALOG_URL,
        modifier=planetary_computer.sign_inplace,
    )

    search = catalog.search(
        collections=["jrc-gsw"],
        intersects=shp.union_all(),
    )

    items = search.item_collection()
    logging.info(f"Found {len(items)} JRC items")

    return odc.stac.stac_load(
        items,
        bands=bands,
        intersects=shp.union_all(),
        chunks=chunks,
    )
