"""Tests for sequential multi-year clear-sky processing."""

from pathlib import Path

import numpy as np
import rasterio
import rioxarray  # noqa: F401
import xarray as xr
from rasterio.transform import from_origin

from data_pipeline.run_multiyear import compute_clear_sky_counts, merge_count_cogs


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
