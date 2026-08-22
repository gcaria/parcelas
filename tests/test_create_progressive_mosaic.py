"""Tests for progressive Sentinel-2 preview mosaic creation."""

from unittest.mock import Mock, patch

from data_pipeline.create_progressive_mosaic import main, select_latest_cogs


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


@patch("data_pipeline.create_progressive_mosaic.fsspec.open")
@patch("data_pipeline.create_progressive_mosaic.MosaicJSON")
@patch("data_pipeline.create_progressive_mosaic.fsspec.core.url_to_fs")
def test_main_globs_unexpanded_input_pattern(mock_url_to_fs, mock_mosaic, mock_open):
    filesystem = Mock()
    filesystem.glob.return_value = ["bucket/previews/run-101/sentinel2_19HCD_uint8.tif"]
    mock_url_to_fs.return_value = (
        filesystem,
        "bucket/previews/run-*/sentinel2_*_uint8.tif",
    )
    mock_mosaic.from_urls.return_value.model_dump_json.return_value = "{}"

    assert (
        main(
            [
                "--input-pattern",
                "gs://bucket/previews/run-*/*.tif",
                "--output",
                "result.gz",
            ]
        )
        == 0
    )

    filesystem.glob.assert_called_once_with(
        "bucket/previews/run-*/sentinel2_*_uint8.tif"
    )
