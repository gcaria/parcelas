"""Tests for sequential multi-year clear-sky processing."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
import rioxarray  # noqa: F401
import xarray as xr
from rasterio.transform import from_origin

from data_pipeline.run_multiyear import (
    accumulate_counts,
    compute_clear_sky_counts,
    finalize_count_accumulator,
    merge_count_cogs,
    run_sequential_multiyear,
)


def test_compute_clear_sky_counts_preserves_denominator():
    data = xr.DataArray(
        np.array(
            [
                [[4, 0], [8, np.nan]],
                [[5, 4], [9, np.nan]],
                [[8, 5], [0, np.nan]],
            ],
            dtype="float32",
        ),
        dims=("time", "y", "x"),
        coords={"time": [0, 1, 2], "y": [1, 0], "x": [0, 1]},
        attrs={"clear_sky_flags": [4, 5], "nodata": 0},
    ).rio.write_crs("EPSG:32719")

    counts = compute_clear_sky_counts(data)

    np.testing.assert_array_equal(counts.sel(band=1), [[2, 2], [0, 0]])
    np.testing.assert_array_equal(counts.sel(band=2), [[3, 2], [2, 0]])


def _write_counts(path: Path, clear: np.ndarray, valid: np.ndarray) -> None:
    profile = {
        "driver": "GTiff",
        "width": clear.shape[1],
        "height": clear.shape[0],
        "count": 2,
        "dtype": "uint16",
        "crs": "EPSG:32719",
        "transform": from_origin(300000, 6300000, 20, 20),
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as destination:
        destination.write(clear.astype("uint16"), 1)
        destination.write(valid.astype("uint16"), 2)


def test_merge_count_cogs_weights_years_by_valid_observations(tmp_path):
    first = tmp_path / "2020.tif"
    second = tmp_path / "2021.tif"
    output = tmp_path / "five-year.tif"
    _write_counts(first, np.array([[1, 8]]), np.array([[2, 10]]))
    _write_counts(second, np.array([[8, 0]]), np.array([[8, 0]]))

    merge_count_cogs([first, second], output)

    with rasterio.open(output) as result:
        np.testing.assert_array_equal(
            result.read(1), np.array([[90, 80]], dtype="uint8")
        )
        assert result.driver == "GTiff"
        assert result.profile["dtype"] == "uint8"
        assert result.nodata == 0


def _count_data(clear: np.ndarray, valid: np.ndarray) -> xr.DataArray:
    return xr.DataArray(
        np.stack([clear, valid]).astype("uint16"),
        dims=("band", "y", "x"),
        coords={"band": [1, 2], "y": [30, 10], "x": [10, 30]},
        attrs={
            "aoi_wkt": "POLYGON ((-100 -100, 100 -100, 100 100, -100 100, -100 -100))",
            "aoi_crs": "EPSG:32719",
        },
    ).rio.write_crs("EPSG:32719")


def test_windowed_accumulator_weights_years_without_annual_rasters(tmp_path):
    accumulator = tmp_path / "counts.tif"
    output = tmp_path / "result.tif"
    first = _count_data(np.array([[1, 8], [0, 2]]), np.array([[2, 10], [0, 4]]))
    second = _count_data(np.array([[8, 0], [0, 1]]), np.array([[8, 0], [0, 2]]))

    accumulate_counts(first, accumulator, buffer=0, reset=True)
    accumulate_counts(second, accumulator, buffer=0)
    finalize_count_accumulator(accumulator, output)

    with rasterio.open(accumulator) as counts:
        assert counts.dtypes == ("uint32", "uint32")
        np.testing.assert_array_equal(counts.read(1), [[9, 8], [0, 3]])
        np.testing.assert_array_equal(counts.read(2), [[10, 10], [0, 6]])
    with rasterio.open(output) as result:
        np.testing.assert_array_equal(result.read(1), [[90, 80], [0, 50]])


def test_multiyear_applies_static_water_mask_once_after_merge(tmp_path):
    annual_data = xr.DataArray(
        np.array([[[4]]], dtype="uint8"),
        dims=("time", "y", "x"),
        coords={"time": [0], "y": [0], "x": [0]},
        attrs={
            "clear_sky_flags": [4, 5],
            "nodata": 0,
            "aoi_wkt": "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))",
            "aoi_crs": "EPSG:4326",
        },
    ).rio.write_crs("EPSG:32719")

    with (
        patch("data_pipeline.run_multiyear._load_aoi") as load_aoi,
        patch(
            "data_pipeline.run_multiyear.get_satellite_data",
            return_value=annual_data,
        ) as get_data,
        patch("data_pipeline.run_multiyear.accumulate_counts") as accumulate,
        patch("data_pipeline.run_multiyear.finalize_count_accumulator") as finalize,
        patch("data_pipeline.run_multiyear.apply_surface_water_mask") as apply_mask,
    ):
        run_sequential_multiyear(
            tile_id="T19HCD",
            start_year=2020,
            end_year=2024,
            output=tmp_path / "result.tif",
            work_dir=tmp_path / "counts",
        )

    assert get_data.call_count == 5
    assert all(call.kwargs["mask_water"] is False for call in get_data.call_args_list)
    assert accumulate.call_count == 5
    assert accumulate.call_args_list[0].kwargs["reset"] is True
    assert all(call.kwargs["reset"] is False for call in accumulate.call_args_list[1:])
    finalize.assert_called_once_with(
        tmp_path / "counts" / "sentinel2_19HCD_counts.tif",
        tmp_path / "result.tif",
    )
    apply_mask.assert_called_once_with(
        tmp_path / "result.tif", load_aoi.return_value, {"x": 1024, "y": 1024}
    )
