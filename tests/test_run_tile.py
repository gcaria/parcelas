"""Tests for the clear-sky tile job CLI."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import geopandas as gpd
import pytest
from shapely.geometry import box

from data_pipeline import run_tile


@pytest.fixture
def sample_geometry():
    """Create a sample AOI geometry."""
    return gpd.GeoDataFrame(geometry=[box(-120, 35, -119, 36)], crs="EPSG:4326")


def test_landsat_requires_path_and_row():
    """Validate Landsat tile identifiers."""
    with pytest.raises(SystemExit):
        run_tile.main(["--sensor", "landsat", "--path", "233"])


def test_sentinel2_requires_tile_id():
    """Validate that --tile-id is required for Sentinel-2."""
    with pytest.raises(SystemExit):
        run_tile.main(["--sensor", "sentinel2", "--aoi-geojson", "aoi.geojson"])


@patch("data_pipeline.run_tile.run_clear_sky_pipeline")
def test_landsat_cli_wires_pipeline_arguments(
    mock_run_clear_sky_pipeline, sample_geometry, monkeypatch
):
    """Run a mocked Landsat job through the CLI."""
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)
    mock_run_clear_sky_pipeline.return_value = "gs://bucket/cogs/landsat_233_087.tif"

    result = run_tile.main(
        [
            "--sensor",
            "landsat",
            "--path",
            "233",
            "--row",
            "87",
            "--time-range",
            "2020-01-01/2020-01-31",
            "--output-template",
            "gs://bucket/cogs/{tile_key}.tif",
            "--chunk-x",
            "128",
            "--chunk-y",
            "256",
            "--buffer",
            "-250",
            "--no-mask-water",
        ]
    )

    assert result == 0
    mock_run_clear_sky_pipeline.assert_called_once_with(
        path=233,
        row=87,
        tile_id=None,
        sensor="landsat",
        aoi_geojson=None,
        time_range="2020-01-01/2020-01-31",
        bands=None,
        chunks={"x": 128, "y": 256},
        mask_water=False,
        output_template="gs://bucket/cogs/{tile_key}.tif",
        buffer=-250,
    )


@patch("data_pipeline.run_tile.run_clear_sky_pipeline")
def test_sentinel2_cli_wires_pipeline_arguments(
    mock_run_clear_sky_pipeline, sample_geometry, monkeypatch
):
    """Run a mocked Sentinel-2 job through the CLI."""
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)
    mock_run_clear_sky_pipeline.return_value = "gs://bucket/cogs/sentinel2_19HCD.tif"

    result = run_tile.main(
        [
            "--sensor",
            "sentinel2",
            "--tile-id",
            "T19HCD",
            "--aoi-geojson",
            "gs://bucket/aoi.geojson",
            "--output-template",
            "gs://bucket/cogs/{tile_key}.tif",
        ]
    )

    assert result == 0
    mock_run_clear_sky_pipeline.assert_called_once_with(
        path=None,
        row=None,
        tile_id="T19HCD",
        sensor="sentinel2",
        aoi_geojson="gs://bucket/aoi.geojson",
        time_range=run_tile.DEFAULT_TIME_RANGE,
        bands=None,
        chunks={"x": 512, "y": 512},
        mask_water=True,
        output_template="gs://bucket/cogs/{tile_key}.tif",
        buffer=-500,
    )


def test_dask_client_not_created_without_scheduler(monkeypatch):
    """Default to local Dask execution when no scheduler is configured."""
    monkeypatch.delenv("DASK_SCHEDULER_ADDRESS", raising=False)

    assert run_tile.connect_dask_from_env() is None


def test_dask_client_created_from_scheduler_env(monkeypatch):
    """Connect to the configured distributed Dask scheduler."""
    mock_client_class = Mock()
    mock_client = Mock()
    mock_client_class.return_value = mock_client
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://scheduler:8786")
    monkeypatch.setitem(
        sys.modules, "distributed", SimpleNamespace(Client=mock_client_class)
    )

    assert run_tile.connect_dask_from_env() == mock_client
    mock_client_class.assert_called_once_with("tcp://scheduler:8786")


@patch("data_pipeline.run_tile.run_clear_sky_pipeline")
def test_dask_client_is_closed_after_pipeline(
    mock_run_clear_sky_pipeline, sample_geometry, monkeypatch
):
    """Close a distributed client after the tile job completes."""
    mock_client = Mock()
    monkeypatch.setenv("DASK_SCHEDULER_ADDRESS", "tcp://scheduler:8786")
    monkeypatch.setitem(
        sys.modules,
        "distributed",
        SimpleNamespace(Client=Mock(return_value=mock_client)),
    )
    mock_run_clear_sky_pipeline.return_value = "gs://bucket/cogs/landsat_233_087.tif"

    run_tile.main(["--sensor", "landsat", "--path", "233", "--row", "87"])

    mock_client.close.assert_called_once_with()
