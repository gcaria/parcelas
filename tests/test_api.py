from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import (
    _chirps_precipitation_map,
    _is_public_path,
    _is_rate_limit_exempt,
    _terraclimate_temperature_map,
    app,
    rate_limit_storage,
)

client = TestClient(app)


# clears the in-memory store before each test
@pytest.fixture(autouse=True)
def reset_rate_limit():
    rate_limit_storage.clear()
    yield
    rate_limit_storage.clear()


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_api_key():
    response = client.get("/mosaicjson/validate?gcs_path=gs://anything")
    assert response.status_code == 401


def test_valid_api_key():
    response = client.get(
        "/mosaicjson/validate?gcs_path=gs://anything", headers={"X-API-Key": "test-key"}
    )
    # should pass auth, fail on GCS (which is mocked)
    assert response.status_code != 401


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/chirps/precipitation/map",
        "/terraclimate/temperature/map",
        "/mosaicjson/sensors",
        "/mosaicjson/info",
        "/mosaicjson/tiles/WebMercatorQuad/8/77/152.png",
        "/mosaicjson/point/-71.543,-35.675",
    ],
)
def test_frontend_read_routes_are_public(path):
    assert _is_public_path(path)


@pytest.mark.parametrize("path", ["/mosaicjson/generate", "/mosaicjson/validate"])
def test_administrative_routes_are_private(path):
    assert not _is_public_path(path)
    assert client.request("POST", path).status_code == 401


def test_generate_missing_cog_storage_url():
    with patch.dict("os.environ", {"COG_STORAGE_URL": ""}):
        response = client.post(
            "/mosaicjson/generate", headers={"X-API-Key": "test-key"}
        )
        assert response.json() == {"error": "COG_STORAGE_URL not configured"}


def test_generate_mosaic_filters_by_sensor():
    with (
        patch.dict("os.environ", {"COG_STORAGE_URL": "gs://bucket/cogs"}),
        patch(
            "api.main.fs.glob",
            return_value=["bucket/cogs/sentinel2_19HCD_uint8.tif"],
        ) as mock_glob,
        patch("api.main.MosaicJSON") as mock_mosaic_json,
    ):
        mock_mosaic = mock_mosaic_json.from_urls.return_value
        mock_mosaic.model_dump.return_value = {"tiles": {}}

        response = client.post(
            "/mosaicjson/generate?sensor=sentinel2",
            headers={"X-API-Key": "test-key"},
        )

        assert response.status_code == 200
        assert response.json() == {"tiles": {}}
        mock_glob.assert_called_once_with("gs://bucket/cogs/sentinel2_*_uint8.tif")
        mock_mosaic_json.from_urls.assert_called_once_with(
            ["gs://bucket/cogs/sentinel2_19HCD_uint8.tif"]
        )


def test_generate_mosaic_rejects_unknown_sensor():
    with patch.dict("os.environ", {"COG_STORAGE_URL": "gs://bucket/cogs"}):
        response = client.post(
            "/mosaicjson/generate?sensor=modis",
            headers={"X-API-Key": "test-key"},
        )

        assert response.status_code == 400
        assert "Unsupported sensor" in response.json()["detail"]


def test_list_mosaic_sensors():
    with patch.dict("os.environ", {"COG_STORAGE_URL": "gs://bucket/cogs"}):
        response = client.get("/mosaicjson/sensors")

        assert response.status_code == 200
        assert response.json() == {
            "sensors": [
                {
                    "id": "landsat",
                    "label": "Landsat",
                    "mosaic_url": "gs://bucket/cogs/mosaics/mosaic_landsat_uint8.json.gz",
                },
                {
                    "id": "sentinel2",
                    "label": "Sentinel-2",
                    "mosaic_url": "gs://bucket/cogs/mosaics/mosaic_sentinel2_uint8.json.gz",
                },
            ]
        }


def test_rate_limit():
    for _ in range(100):
        client.get(
            "/mosaicjson/validate?gcs_path=gs://anything",
            headers={"X-API-Key": "test-key"},
        )
    response = client.get(
        "/mosaicjson/validate?gcs_path=gs://anything", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded"}


def test_rate_limit_isolated_by_forwarded_client_ip():
    """Do not share a rate-limit bucket across clients behind Cloud Run."""
    first_client = {"X-API-Key": "test-key", "X-Forwarded-For": "203.0.113.10"}
    second_client = {"X-API-Key": "test-key", "X-Forwarded-For": "203.0.113.11"}

    for _ in range(100):
        response = client.get(
            "/mosaicjson/validate?gcs_path=gs://anything", headers=first_client
        )
        assert response.status_code != 429

    assert (
        client.get(
            "/mosaicjson/validate?gcs_path=gs://anything", headers=first_client
        ).status_code
        == 429
    )
    assert (
        client.get(
            "/mosaicjson/validate?gcs_path=gs://anything", headers=second_client
        ).status_code
        != 429
    )


def test_map_reads_are_exempt_from_ip_rate_limit():
    assert _is_rate_limit_exempt("/mosaicjson/tiles/WebMercatorQuad/8/77/152.png")
    assert _is_rate_limit_exempt("/mosaicjson/point/-71.543,-35.675")
    assert not _is_rate_limit_exempt("/mosaicjson/validate")


def test_chirps_precipitation_map():
    expected = {
        "tile_url": "https://earthengine.example/{z}/{x}/{y}",
        "min_mm": 0,
        "max_mm": 3000,
        "palette": ["fff7ec"],
        "period": "2020–2024",
    }
    with patch("api.main._chirps_precipitation_map", return_value=expected):
        response = client.get("/chirps/precipitation/map")

    assert response.status_code == 200
    assert response.json() == expected


def test_chirps_precipitation_expression():
    ee = MagicMock()
    with patch.dict("sys.modules", {"ee": ee}):
        _chirps_precipitation_map.cache_clear()
        collection = ee.ImageCollection.return_value
        image = collection.filterDate.return_value.select.return_value.sum.return_value
        annual_precipitation = image.divide.return_value.rename.return_value
        result = annual_precipitation.updateMask.return_value
        result.getMapId.return_value = {
            "tile_fetcher": type(
                "TileFetcher",
                (),
                {"url_format": "https://earthengine.example/{z}/{x}/{y}"},
            )()
        }

        response = _chirps_precipitation_map()

        ee.Initialize.assert_called_once()
        ee.ImageCollection.assert_called_once_with("UCSB-CHC/CHIRPS/V3/DAILY_RNL")
        collection.filterDate.assert_called_once_with("2020-01-01", "2025-01-01")
        collection.filterDate.return_value.select.assert_called_once_with(
            "precipitation"
        )
        image.divide.assert_called_once_with(5)
        ee.Image.assert_called_once_with("JRC/GSW1_4/GlobalSurfaceWater")
        occurrence = ee.Image.return_value.select
        occurrence.assert_called_once_with("occurrence")
        unmasked_occurrence = occurrence.return_value.unmask
        unmasked_occurrence.assert_called_once_with(0)
        non_water = unmasked_occurrence.return_value.lt.return_value
        unmasked_occurrence.return_value.lt.assert_called_once_with(90)
        annual_precipitation.updateMask.assert_called_once_with(non_water)
        assert response["period"] == "2020–2024"
        _chirps_precipitation_map.cache_clear()


def test_terraclimate_temperature_map():
    expected = {
        "tile_url": "https://earthengine.example/{z}/{x}/{y}",
        "min_c": -5,
        "max_c": 25,
        "palette": ["313695"],
        "period": "2020–2024",
    }
    with patch("api.main._terraclimate_temperature_map", return_value=expected):
        response = client.get("/terraclimate/temperature/map")

    assert response.status_code == 200
    assert response.json() == expected


def test_terraclimate_temperature_expression():
    ee = MagicMock()
    with patch.dict("sys.modules", {"ee": ee}):
        _terraclimate_temperature_map.cache_clear()
        collection = ee.ImageCollection.return_value.filterDate.return_value
        weighted_collection = collection.map.return_value
        annual_temperature = (
            weighted_collection.sum.return_value.divide.return_value.rename.return_value
        )
        result = annual_temperature.updateMask.return_value
        result.getMapId.return_value = {
            "tile_fetcher": type(
                "TileFetcher",
                (),
                {"url_format": "https://earthengine.example/{z}/{x}/{y}"},
            )()
        }

        response = _terraclimate_temperature_map()

        ee.ImageCollection.assert_called_once_with("IDAHO_EPSCOR/TERRACLIMATE")
        ee.ImageCollection.return_value.filterDate.assert_called_once_with(
            "2020-01-01", "2025-01-01"
        )
        weighted_month = collection.map.call_args.args[0]
        month = MagicMock()
        weighted_month_result = weighted_month(month)
        month.get.assert_called_once_with("system:time_start")
        ee.Date.assert_called_once_with(month.get.return_value)
        date = ee.Date.return_value
        date.advance.assert_called_once_with(1, "month")
        date.advance.return_value.difference.assert_called_once_with(date, "day")
        month.select.assert_called_once_with(["tmmn", "tmmx"])
        month.select.return_value.reduce.assert_called_once_with(ee.Reducer.mean())
        scaled_mean = month.select.return_value.reduce.return_value.multiply
        scaled_mean.assert_called_once_with(0.1)
        days = date.advance.return_value.difference.return_value
        scaled_mean.return_value.multiply.assert_called_once_with(days)
        assert weighted_month_result is scaled_mean.return_value.multiply.return_value
        weighted_collection.sum.return_value.divide.assert_called_once_with(1827)
        annual_temperature.updateMask.assert_called_once()
        assert response["period"] == "2020–2024"
        _terraclimate_temperature_map.cache_clear()
