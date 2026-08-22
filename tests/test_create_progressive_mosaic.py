"""Tests for progressive Sentinel-2 preview mosaic creation."""

from data_pipeline.create_progressive_mosaic import select_latest_cogs


def test_select_latest_cogs_keeps_newest_run_per_tile():
    paths = [
        "parcelas-wrs2/previews/run-100/sentinel2_19HCD_uint8.tif",
        "parcelas-wrs2/previews/run-105/sentinel2_19HCD_uint8.tif",
        "parcelas-wrs2/previews/run-103/sentinel2_18HYC_uint8.tif",
        "parcelas-wrs2/previews/run-999/landsat_233_087_uint8.tif",
        "parcelas-wrs2/previews/run-invalid/sentinel2_19HCC_uint8.tif",
    ]

    assert select_latest_cogs(paths) == [
        "gs://parcelas-wrs2/previews/run-103/sentinel2_18HYC_uint8.tif",
        "gs://parcelas-wrs2/previews/run-105/sentinel2_19HCD_uint8.tif",
    ]
