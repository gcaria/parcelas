"""This module contains functions for fetching Landsat 8 and 9 data from the Microsoft
Planetary Computer, computing the percentage of clear sky pixels.
"""

import logging
from typing import List

import geopandas
import odc.stac
import planetary_computer
import pystac_client
import rioxarray  # noqa: F401
import xarray

from data_pipeline.shapefiles import get_wrs2_tile

CLEAR_SKY_QA_FLAGS = [
    21824,  # clear with lows set
    21826,  # dilated cloud over land
    21888,  # water with lows set
    21890,  # dilated cloud over water
    30048,  # high conf snow/ice
    54596,  # high conf cirrus
]
PLANETARY_COMPUTER_CATALOG_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def get_landsat_data(
    shp: "geopandas.GeoDataFrame",
    path: int,
    row: int,
    time_range: str = "2020-01-01/2020-12-31",
    bands: List[str] = ["qa_pixel"],
    chunks: dict = {"x": 512, "y": 512},
    mask_water: bool = True,
) -> "xarray.Dataset":
    """
    Fetches Landsat 8 and 9 data from the Microsoft Planetary Computer for a specified
    WRS-2 tile and time range, and returns it as an xarray Dataset.

    Args:
        shp: A GeoDataFrame containing the geometry of the area of interest.
        path: The WRS-2 path number.
        row: The WRS-2 row number.
        time_range: The time range for which to fetch data, in the format "YYYY-MM-DD/YYYY-MM-DD".
        bands: A list of band names to fetch.
        chunks: A dictionary specifying the chunk sizes for the xarray Dataset.
        mask_water: A boolean indicating whether to mask out water pixels based on the JRC Global Surface Water dataset.

    Returns:
        An xarray Dataset containing the requested Landsat data.
    """
    catalog = pystac_client.Client.open(
        PLANETARY_COMPUTER_CATALOG_URL,
        modifier=planetary_computer.sign_inplace,
    )

    search = catalog.search(
        collections=["landsat-c2-l2"],
        intersects=shp.union_all(),
        datetime=time_range,
        query={
            "landsat:wrs_path": {"eq": f"{path:03d}"},
            "landsat:wrs_row": {"eq": f"{row:03d}"},
            "platform": {"in": ["landsat-8", "landsat-9"]},
        },
    )

    items = search.item_collection()

    da_ls = odc.stac.stac_load(
        items,
        bands=bands,
        intersects=shp.union_all(),
        chunks=chunks,
        nodata=65535,
    )["qa_pixel"]

    da_sw = get_jrc_surface_water(shp, chunks=chunks)["occurrence"]
    da_sw = da_sw.rio.reproject_match(da_ls).squeeze()
    if mask_water:
        da_ls = da_ls.where(da_sw > 90)

    return da_ls


def compute_clear_sky_percentage(
    da_ls: "xarray.Dataset", clear_sky_qa_flags: List[int] = CLEAR_SKY_QA_FLAGS
) -> "xarray.DataArray":
    """
    Computes the percentage of clear sky pixels in a given xarray Dataset containing
    Landsat data.

    Args:
        data_ls: An xarray Dataset containing the Landsat data, with a "qa_pixel" variable.
        clear_sky_qa_flags: A list of QA flag values that indicate clear sky conditions.

    Returns:
        An xarray DataArray containing the percentage of clear sky pixels for each spatial location.
    """
    clear_sky = da_ls.isin(clear_sky_qa_flags)
    _sum = clear_sky.astype(int).sum(dim="time")

    return _sum / len(da_ls.time)


def store_clear_sky_percentage(
    da_csp: "xarray.DataArray",
    path: int,
    row: int,
    output_template: str = "{path:03d}_{row:03d}.tif",
) -> None:
    """
    Stores the clear sky percentage data as a Cloud Optimized GeoTIFF (COG) file.

    Args:
        da_csp (xarray.DataArray): An xarray DataArray containing the percentage of clear sky pixels for each spatial location.
        path (int): The WRS-2 path number.
        row (int): The WRS-2 row number.
        output_template (str): A template string for the output file name, with placeholders for path and row.

    Returns:
        None
    """
    da_csp = (da_csp.where(da_csp > 0) * 100).fillna(0)
    da_csp = da_csp.astype("uint8").rio.write_nodata(0)

    poly = get_wrs2_tile(path, row)
    poly = poly.to_crs(da_csp.rio.crs).geometry.iloc[0].simplify(tolerance=1000)
    poly = poly.buffer(-300)

    da_csp = da_csp.rio.clip([poly], da_csp.rio.crs, drop=True)

    fname = output_template.format(path=path, row=row)
    da_csp.rio.to_raster(fname, driver="COG")

    logging.info(f"Clear sky percentage stored at {fname}")


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
