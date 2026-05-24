"""Tests for the clear sky data module."""

from unittest.mock import Mock, patch

import geopandas as gpd
import numpy as np
import pytest
import xarray as xr
from shapely.geometry import box

from data_pipeline.clear_sky import (
    compute_clear_sky_percentage,
    format_satellite_tile_key,
    get_jrc_surface_water,
    get_landsat_data,
    get_satellite_data,
    run_clear_sky_pipeline,
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
                [21824, 21824, 0],  # time 2: five clear sky pixels
                [21826, 21824, 0],
                [0, 0, 21824],
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
            "y": [35.25, 35.5, 35.75],
            "x": [-119.75, -119.5, -119.25],
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


def test_format_satellite_tile_key_landsat():
    """Test formatting Landsat output tile keys."""
    assert format_satellite_tile_key("landsat", path=42, row=35) == "landsat_042_035"


def test_format_satellite_tile_key_sentinel2():
    """Test formatting Sentinel-2 output tile keys."""
    assert format_satellite_tile_key("sentinel2", tile_id="T19HCD") == "sentinel2_19HCD"


def test_format_satellite_tile_key_validates_identifiers():
    """Test tile key identifier validation."""
    with pytest.raises(ValueError, match="path and row"):
        format_satellite_tile_key("landsat", path=42)

    with pytest.raises(ValueError, match="tile_id"):
        format_satellite_tile_key("sentinel2")


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
@patch("rioxarray.raster_array.RasterArray.to_raster")
def test_store_clear_sky_percentage(
    mock_to_raster, mock_logging, mock_get_wrs2, sample_qa_dataarray
):
    """Test storing clear sky percentage as COG."""
    # Mock the WRS2 tile
    mock_gdf = gpd.GeoDataFrame(geometry=[box(-121, 34, -118, 37)], crs="EPSG:4326")
    mock_get_wrs2.return_value = mock_gdf

    da_csp = compute_clear_sky_percentage(sample_qa_dataarray)
    da_csp = da_csp.rio.write_crs("EPSG:4326")

    output_path = store_clear_sky_percentage(da_csp, path=42, row=35)

    assert output_path == "landsat_042_035.tif"
    mock_to_raster.assert_called_once_with("landsat_042_035.tif", driver="COG")
    mock_logging.info.assert_called_once()


@patch("data_pipeline.clear_sky.logging")
@patch("rioxarray.raster_array.RasterArray.to_raster")
def test_store_clear_sky_percentage_sentinel2(
    mock_to_raster, mock_logging, sample_qa_dataarray, sample_geometry
):
    """Test storing Sentinel-2 clear sky percentage with tile key naming."""
    da_csp = compute_clear_sky_percentage(sample_qa_dataarray)
    da_csp = da_csp.rio.write_crs("EPSG:4326")

    output_path = store_clear_sky_percentage(
        da_csp,
        tile_id="T19HCD",
        sensor="sentinel2",
        output_template="gs://bucket/cogs/{tile_key}_uint8.tif",
        clip_shp=sample_geometry,
    )

    assert output_path == "gs://bucket/cogs/sentinel2_19HCD_uint8.tif"
    mock_to_raster.assert_called_once_with(
        "gs://bucket/cogs/sentinel2_19HCD_uint8.tif", driver="COG"
    )
    mock_logging.info.assert_called_once()


def test_store_clear_sky_percentage_sentinel2_requires_clip_shp(sample_qa_dataarray):
    """Test Sentinel-2 storage requires explicit clipping geometry."""
    da_csp = compute_clear_sky_percentage(sample_qa_dataarray)
    da_csp = da_csp.rio.write_crs("EPSG:4326")
    da_csp.rio.write_nodata = Mock(return_value=da_csp)

    with pytest.raises(ValueError, match="clip_shp"):
        store_clear_sky_percentage(da_csp, tile_id="T19HCD", sensor="sentinel2")


@patch("data_pipeline.clear_sky.store_clear_sky_percentage")
@patch("data_pipeline.clear_sky.compute_clear_sky_percentage")
@patch("data_pipeline.clear_sky.get_satellite_data")
def test_run_clear_sky_pipeline(
    mock_get_satellite_data,
    mock_compute_clear_sky_percentage,
    mock_store_clear_sky_percentage,
    sample_geometry,
):
    """Test the unified clear sky pipeline."""
    mock_da_sat = Mock()
    mock_da_csp = Mock()
    mock_get_satellite_data.return_value = mock_da_sat
    mock_compute_clear_sky_percentage.return_value = mock_da_csp
    mock_store_clear_sky_percentage.return_value = (
        "gs://bucket/cogs/sentinel2_19HCD.tif"
    )

    result = run_clear_sky_pipeline(
        shp=sample_geometry,
        tile_id="T19HCD",
        sensor="sentinel2",
        time_range="2020-01-01/2020-12-31",
        output_template="gs://bucket/cogs/{tile_key}.tif",
    )

    assert result == "gs://bucket/cogs/sentinel2_19HCD.tif"
    mock_get_satellite_data.assert_called_once_with(
        shp=sample_geometry,
        path=None,
        row=None,
        tile_id="T19HCD",
        sensor="sentinel2",
        time_range="2020-01-01/2020-12-31",
        bands=None,
        chunks={"x": 512, "y": 512},
        mask_water=True,
    )
    mock_compute_clear_sky_percentage.assert_called_once_with(mock_da_sat)
    mock_store_clear_sky_percentage.assert_called_once_with(
        da_csp=mock_da_csp,
        path=None,
        row=None,
        tile_id="T19HCD",
        sensor="sentinel2",
        output_template="gs://bucket/cogs/{tile_key}.tif",
        buffer=-500,
        clip_shp=sample_geometry,
    )
