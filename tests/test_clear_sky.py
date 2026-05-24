"""Tests for the clear sky data module."""

from unittest.mock import Mock, patch

import geopandas as gpd
import numpy as np
import pytest
import xarray as xr
from shapely.geometry import box

from data_pipeline.clear_sky import (
    compute_clear_sky_percentage,
    get_jrc_surface_water,
    get_landsat_data,
    get_satellite_data,
    store_clear_sky_percentage,
)


@pytest.fixture
def sample_geometry():
    """Create a sample geometry for testing."""
    return gpd.GeoDataFrame(geometry=[box(-120, 35, -119, 36)], crs="EPSG:4326")


@pytest.fixture
def sample_qa_dataarray():
    """Create a sample QA pixel DataArray for testing."""
    # Create a simple 2D x 3x3 array with some clear sky flags
    data = np.array(
        [
            [
                [21824, 21824, 0],  # time 1: two clear sky pixels
                [0, 21824, 0],
                [0, 0, 21824],
            ],
            [
                [0, 0, 21826],  # time 2: one clear sky pixel
                [21826, 0, 0],
                [0, 0, 0],
            ],
            [
                [0, 0, 0],  # time 3: no clear sky pixels
                [0, 0, 0],
                [0, 0, 0],
            ],
        ]
    )

    return xr.DataArray(
        data,
        dims=("time", "y", "x"),
        coords={
            "time": ["2020-01-01", "2020-02-01", "2020-03-01"],
            "y": range(3),
            "x": range(3),
        },
    )


def test_compute_clear_sky_percentage(sample_qa_dataarray):
    """Test computation of clear sky percentage."""
    result = compute_clear_sky_percentage(sample_qa_dataarray)

    # Expected: 2/3, 1/3, 0/3 for each pixel column
    expected = np.array(
        [
            [2 / 3, 2 / 3, 0 / 3],
            [1 / 3, 2 / 3, 0 / 3],
            [0 / 3, 0 / 3, 2 / 3],
        ]
    )

    np.testing.assert_allclose(result.values, expected)
    assert isinstance(result, xr.DataArray)
    assert "y" in result.dims
    assert "x" in result.dims
    assert "time" not in result.dims


def test_compute_clear_sky_percentage_custom_flags(sample_qa_dataarray):
    """Test with custom QA flags."""
    custom_flags = [21824]  # Only one flag
    result = compute_clear_sky_percentage(sample_qa_dataarray, custom_flags)

    expected = np.array(
        [
            [2 / 3, 2 / 3, 0 / 3],
            [0 / 3, 2 / 3, 0 / 3],
            [0 / 3, 0 / 3, 2 / 3],
        ]
    )

    np.testing.assert_allclose(result.values, expected)


def test_compute_clear_sky_percentage_uses_dataarray_flags(sample_qa_dataarray):
    """Test using clear sky flags from DataArray attrs."""
    sample_qa_dataarray.attrs["clear_sky_flags"] = [21824]

    result = compute_clear_sky_percentage(sample_qa_dataarray)

    expected = np.array(
        [
            [2 / 3, 2 / 3, 0 / 3],
            [0 / 3, 2 / 3, 0 / 3],
            [0 / 3, 0 / 3, 2 / 3],
        ]
    )

    np.testing.assert_allclose(result.values, expected)


def test_compute_clear_sky_percentage_empty():
    """Test with empty data."""
    da_empty = xr.DataArray(
        np.zeros((0, 3, 3)),
        dims=("time", "y", "x"),
        coords={"time": [], "y": range(3), "x": range(3)},
    )

    with pytest.raises(ValueError, match="empty"):
        compute_clear_sky_percentage(da_empty)


@patch("data_pipeline.clear_sky.pystac_client.Client")
@patch("data_pipeline.clear_sky.odc.stac.stac_load")
def test_get_satellite_data_landsat(mock_stac_load, mock_client, sample_geometry):
    """Test fetching Landsat data through the generalized function."""
    # Mock the STAC client and search
    mock_catalog = Mock()
    mock_client.open.return_value = mock_catalog
    mock_search = Mock()
    mock_catalog.search.return_value = mock_search
    mock_search.item_collection.return_value = ["item1", "item2"]

    # Mock the STAC load
    mock_da = xr.DataArray(
        np.ones((2, 10, 10)),
        dims=("time", "y", "x"),
    )
    mock_stac_load.return_value = {"qa_pixel": mock_da}

    result = get_satellite_data(
        shp=sample_geometry,
        path=42,
        row=35,
        sensor="landsat",
        time_range="2020-01-01/2020-12-31",
        mask_water=False,
    )

    assert isinstance(result, xr.DataArray)
    assert result.attrs["sensor"] == "landsat"
    assert result.attrs["clear_sky_flags"]
    mock_client.open.assert_called_once()
    mock_catalog.search.assert_called_once_with(
        collections=["landsat-c2-l2"],
        intersects=sample_geometry.union_all(),
        datetime="2020-01-01/2020-12-31",
        query={
            "landsat:wrs_path": {"eq": "042"},
            "landsat:wrs_row": {"eq": "035"},
            "platform": {"in": ["landsat-8", "landsat-9"]},
        },
    )
    mock_stac_load.assert_called_once()


@patch("data_pipeline.clear_sky.pystac_client.Client")
@patch("data_pipeline.clear_sky.odc.stac.stac_load")
def test_get_satellite_data_sentinel2(mock_stac_load, mock_client, sample_geometry):
    """Test fetching Sentinel-2 data through the generalized function."""
    mock_catalog = Mock()
    mock_client.open.return_value = mock_catalog
    mock_search = Mock()
    mock_catalog.search.return_value = mock_search
    mock_search.item_collection.return_value = ["item1", "item2"]

    mock_da = xr.DataArray(
        np.ones((2, 10, 10)),
        dims=("time", "y", "x"),
    )
    mock_stac_load.return_value = {"SCL": mock_da}

    result = get_satellite_data(
        shp=sample_geometry,
        tile_id="T19HCD",
        sensor="sentinel2",
        time_range="2020-01-01/2020-12-31",
        mask_water=False,
    )

    assert isinstance(result, xr.DataArray)
    assert result.attrs["sensor"] == "sentinel2"
    assert result.attrs["clear_sky_flags"] == [4, 5, 6, 11]
    mock_catalog.search.assert_called_once_with(
        collections=["sentinel-2-l2a"],
        intersects=sample_geometry.union_all(),
        datetime="2020-01-01/2020-12-31",
        query={"s2:mgrs_tile": {"eq": "19HCD"}},
    )
    mock_stac_load.assert_called_once_with(
        ["item1", "item2"],
        bands=["SCL"],
        intersects=sample_geometry.union_all(),
        chunks={"x": 512, "y": 512},
        nodata=0,
    )


@patch("data_pipeline.clear_sky.pystac_client.Client")
@patch("data_pipeline.clear_sky.odc.stac.stac_load")
def test_get_landsat_data_wrapper(mock_stac_load, mock_client, sample_geometry):
    """Test backward-compatible Landsat wrapper."""
    mock_catalog = Mock()
    mock_client.open.return_value = mock_catalog
    mock_search = Mock()
    mock_catalog.search.return_value = mock_search
    mock_search.item_collection.return_value = ["item1"]
    mock_stac_load.return_value = {
        "qa_pixel": xr.DataArray(np.ones((1, 10, 10)), dims=("time", "y", "x"))
    }

    result = get_landsat_data(
        shp=sample_geometry,
        path=42,
        row=35,
        time_range="2020-01-01/2020-12-31",
        mask_water=False,
    )

    assert result.attrs["sensor"] == "landsat"


def test_get_satellite_data_landsat_requires_path_row(sample_geometry):
    """Test Landsat path and row validation."""
    with pytest.raises(ValueError, match="path and row"):
        get_satellite_data(sample_geometry, sensor="landsat")


def test_get_satellite_data_rejects_unknown_sensor(sample_geometry):
    """Test sensor validation."""
    with pytest.raises(ValueError, match="Unsupported sensor"):
        get_satellite_data(sample_geometry, sensor="modis")  # type: ignore[arg-type]


def test_get_satellite_data_rejects_empty_sentinel2_tile_id(sample_geometry):
    """Test Sentinel-2 tile ID validation."""
    with pytest.raises(ValueError, match="tile_id"):
        get_satellite_data(sample_geometry, sensor="sentinel2", tile_id=" ")


@patch("data_pipeline.clear_sky.pystac_client.Client")
@patch("data_pipeline.clear_sky.odc.stac.stac_load")
def test_get_jrc_surface_water(mock_stac_load, mock_client, sample_geometry):
    """Test fetching JRC surface water data."""
    # Mock the STAC client and search
    mock_catalog = Mock()
    mock_client.open.return_value = mock_catalog
    mock_search = Mock()
    mock_catalog.search.return_value = mock_search
    mock_search.item_collection.return_value = ["item1"]

    # Mock the STAC load
    mock_ds = xr.Dataset({"occurrence": xr.DataArray(np.ones((1, 10, 10)))})
    mock_stac_load.return_value = mock_ds

    result = get_jrc_surface_water(sample_geometry)

    assert isinstance(result, xr.Dataset)
    assert "occurrence" in result.data_vars
    mock_client.open.assert_called_once()
    mock_catalog.search.assert_called_once()


@patch("data_pipeline.clear_sky.get_wrs2_tile")
@patch("data_pipeline.clear_sky.logging")
def test_store_clear_sky_percentage(mock_logging, mock_get_wrs2, sample_qa_dataarray):
    """Test storing clear sky percentage as COG."""
    # Mock the WRS2 tile
    mock_gdf = gpd.GeoDataFrame(geometry=[box(-121, 34, -118, 37)], crs="EPSG:4326")
    mock_get_wrs2.return_value = mock_gdf

    # Mock rioxarray methods
    da_csp = compute_clear_sky_percentage(sample_qa_dataarray)
    da_csp.rio.write_nodata = Mock(return_value=da_csp)
    da_csp.rio.clip = Mock(return_value=da_csp)
    da_csp.rio.to_raster = Mock()

    store_clear_sky_percentage(da_csp, path=42, row=35)

    da_csp.rio.to_raster.assert_called_once()
    mock_logging.info.assert_called_once()
