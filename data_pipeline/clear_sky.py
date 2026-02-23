import logging
from typing import List

import odc.stac
import planetary_computer
import pystac_client

from data_pipeline.shapefiles import get_wrs2_tile

CLEAR_SKY_QA_FLAGS = [
    21824,  # clear with lows set
    21826,  # dilated cloud over land
    21888,  # water with lows set
    21890,  # dilated cloud over water
    30048,  # high conf snow/ice
    54596,  # high conf cirrus
]


def get_landsat_data(
    shp: "geopandas.GeoDataFrame",
    path: int,
    row: int,
    time_range: str = "2020-01-01/2020-12-31",
    bands: List[str] = ["qa_pixel"],
) -> "xarray.Dataset":
    """Fetches Landsat 8 and 9 data from the Microsoft Planetary Computer for a
    specified WRS-2 tile and time range, and returns it as an xarray Dataset.

    Args:
        shp: A GeoDataFrame containing the geometry of the area of interest.
        path: The WRS-2 path number.
        row: The WRS-2 row number.
        time_range: The time range for which to fetch data, in the format "YYYY-MM-DD/YYYY-MM-DD".
        bands: A list of band names to fetch.

    Returns:
        An xarray Dataset containing the requested Landsat data.
    """

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
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

    return odc.stac.stac_load(
        items,
        bands=bands,
        intersects=shp.union_all(),
        chunks={"y": 512, "x": 512},
        nodata=65535,
    )


def compute_clear_sky_percentage(
    data_ls: "xarray.Dataset", clear_sky_qa_flags: List[int] = CLEAR_SKY_QA_FLAGS
) -> "xarray.DataArray":
    """Computes the percentage of clear sky pixels in a given xarray Dataset
    containing Landsat data.

    Args:
        data_ls: An xarray Dataset containing the Landsat data, with a "qa_pixel" variable.
        clear_sky_qa_flags: A list of QA flag values that indicate clear sky conditions.

    Returns:
        An xarray DataArray containing the percentage of clear sky pixels for each spatial location.
    """
    qa = data_ls["qa_pixel"]
    clear_sky = qa.isin(clear_sky_qa_flags)
    _sum = clear_sky.astype(int).sum(dim="time")

    return _sum / len(qa.time)


def store_clear_sky_percentage(
    da_csp: "xarray.DataArray",
    path: int,
    row: int,
    output_template: str = "{path:03d}_{row:03d}.tif",
) -> None:
    """Stores the clear sky percentage data as a Cloud Optimized GeoTIFF (COG)
    file.

    Args:
        da_csp (xarray.DataArray): An xarray DataArray containing the percentage of clear sky pixels for each spatial location.
        path (int): The WRS-2 path number.
        row (int): The WRS-2 row number.
        output_template (str): A template string for the output file name, with placeholders for path and row.

    Returns:
        None
    """
    da_csp = (da_csp.where(da > 0) * 100).fillna(0)
    da_csp = da_csp.astype("uint8").rio.write_nodata(0)

    poly = get_wrs2_tile(path, row)
    poly = poly.to_crs(da_csp.rio.crs).geometry.iloc[0].simplify(tolerance=1000)
    poly = poly.buffer(-300)

    da_csp = da_csp.rio.clip([poly], da_csp.rio.crs, drop=True)

    fname = output_template.format(path=path, row=row)
    da_csp.rio.to_raster(fname, driver="COG")

    logging.info(f"Clear sky percentage stored at {fname}")
