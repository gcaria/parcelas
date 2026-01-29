import logging

import odc.stac
import planetary_computer
import pystac_client


def get_landsat_data(shp, time_range="2020-01-01/2020-12-31", bands=["qa_pixel"]):
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )

    search = catalog.search(
        collections=["landsat-c2-l2"],
        intersects=shp.union_all(),
        datetime=time_range,
    )

    items = search.item_collection()

    # Get rid of L7 data
    items = [i for i in items if "7" not in i.properties["platform"]]
    logging.info(f"Found {len(items)} Items")

    return odc.stac.stac_load(
        items,
        bands=bands,
        intersects=shp.union_all(),
        chunks={"y": 512, "x": 512},
        nodata=65535,
    )


def compute_clear_sky_percentage(
    data_ls,
    clear_sky_qa_flags=[
        21824,  # clear with lows set
        21826,  # dilated cloud over land
        21888,  # water with lows set
        21890,  # dilated cloud over water
        30048,  # high conf snow/ice
        54596,  # high conf cirrus
    ],
):
    qa = data_ls["qa_pixel"]
    clear_sky = qa.isin(clear_sky_qa_flags)
    _sum = clear_sky.astype(int).sum(dim="time")

    return _sum / len(qa.time)
