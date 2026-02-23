import io
import os
import zipfile
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pytest
from shapely.geometry import box

from data_pipeline.shapefiles import (
    download_wrs2_grid,
    get_chile_boundary,
    get_chile_wrs2_tiles,
    get_wrs2_grid,
    get_wrs2_tile,
)

# --- Fixtures ---

@pytest.fixture
def mock_wrs2_gdf():
    return gpd.GeoDataFrame(
        {"PATH": [233, 233], "ROW": [85, 86]},
        geometry=[box(-72, -39, -71, -38), box(-72, -40, -71, -39)],
        crs="EPSG:4326",
    )

@pytest.fixture
def mock_chile_gdf():
    return gpd.GeoDataFrame(
        {"ADMIN": ["Chile"]},
        geometry=[box(-75, -55, -66, -17)],
        crs="EPSG:4326",
    )

# --- Tests ---

def test_get_wrs2_grid(mock_wrs2_gdf):
    with patch("data_pipeline.shapefiles.gpd.read_file", return_value=mock_wrs2_gdf):
        result = get_wrs2_grid()
        assert isinstance(result, gpd.GeoDataFrame)
        assert "PATH" in result.columns
        assert "ROW" in result.columns


def test_get_wrs2_tile(mock_wrs2_gdf):
    with patch("data_pipeline.shapefiles.get_wrs2_grid", return_value=mock_wrs2_gdf):
        result = get_wrs2_tile(233, 85)
        assert len(result) == 1
        assert result.iloc[0]["PATH"] == 233
        assert result.iloc[0]["ROW"] == 85


def test_get_wrs2_tile_not_found(mock_wrs2_gdf):
    with patch("data_pipeline.shapefiles.get_wrs2_grid", return_value=mock_wrs2_gdf):
        result = get_wrs2_tile(999, 999)
        assert len(result) == 0


def test_get_chile_boundary(mock_chile_gdf, tmp_path):
    output = str(tmp_path / "chile.geojson")
    with patch("data_pipeline.shapefiles.gpd.read_file", return_value=mock_chile_gdf):
        result = get_chile_boundary(output_file=output)
        assert isinstance(result, gpd.GeoDataFrame)
        assert "Chile" in result["ADMIN"].values
        assert os.path.exists(output)


def test_get_chile_wrs2_tiles(mock_wrs2_gdf, mock_chile_gdf):
    with patch("data_pipeline.shapefiles.get_wrs2_grid", return_value=mock_wrs2_gdf), \
         patch("data_pipeline.shapefiles.get_chile_boundary", return_value=mock_chile_gdf):
        result = get_chile_wrs2_tiles()
        assert isinstance(result, gpd.GeoDataFrame)
        assert len(result) > 0


def test_download_wrs2_grid_skips_if_exists(tmp_path):
    output = str(tmp_path / "wrs2.geojson")
    open(output, "w").close()  # create empty file
    with patch("data_pipeline.shapefiles.requests.get") as mock_get:
        download_wrs2_grid(output_file=output)
        mock_get.assert_not_called()  # should skip download entirely


def test_download_wrs2_grid_bad_zip(tmp_path):
    output = str(tmp_path / "wrs2.geojson")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"not a zip file"
    with patch("data_pipeline.shapefiles.requests.get", return_value=mock_response):
        with pytest.raises(ValueError, match="not a valid zip"):
            download_wrs2_grid(output_file=output)


def test_download_wrs2_grid_no_shp(tmp_path):
    output = str(tmp_path / "wrs2.geojson")

    # Create a zip with no .shp file
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "no shapefile here")
    buf.seek(0)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = buf.read()

    with patch("data_pipeline.shapefiles.requests.get", return_value=mock_response):
        with pytest.raises(ValueError, match="Could not find a .shp"):
            download_wrs2_grid(output_file=output)
