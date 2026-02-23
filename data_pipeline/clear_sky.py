import logging

import odc.stac
import planetary_computer
import pystac_client

from data_pipeline.shapefiles import get_wrs2_tiles

CLEAR_SKY_QA_FLAGS = [
    21824,  # clear with lows set
    21826,  # dilated cloud over land
    21888,  # water with lows set
    21890,  # dilated cloud over water
    30048,  # high conf snow/ice
    54596,  # high conf cirrus
]


def get_landsat_data(
    shp, path, row, time_range="2020-01-01/2020-12-31", bands=["qa_pixel"]
):
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


def compute_clear_sky_percentage(data_ls, clear_sky_qa_flags=CLEAR_SKY_QA_FLAGS):
    qa = data_ls["qa_pixel"]
    clear_sky = qa.isin(clear_sky_qa_flags)
    _sum = clear_sky.astype(int).sum(dim="time")

    return _sum / len(qa.time)


def store_clear_sky_percentage(
    da_csp, path, row, output_template="{path:03d}_{row:03d}.tif"
):
    da_csp = (da_csp.where(da > 0) * 100).fillna(0)
    da_csp = da_csp.astype("uint8").rio.write_nodata(0)

    poly = get_wrs2_tiles(path, row)
    poly = poly.to_crs(da_csp.rio.crs).geometry.iloc[0].simplify(tolerance=1000)
    poly = poly.buffer(-300)

    da_csp = da_csp.rio.clip([poly], da_csp.rio.crs, drop=True)

    fname = output_template.format(path=path, row=row)
    da_csp.rio.to_raster(fname, driver="COG")

    logging.info(f"Clear sky percentage stored at {fname}")
