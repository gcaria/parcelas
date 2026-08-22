from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import _is_public_path, _is_rate_limit_exempt, app, rate_limit_storage

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
        "/mosaicjson/sensors",
        "/mosaicjson/info",
        "/mosaicjson/tiles/WebMercatorQuad/8/77/152.png",
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


def test_tile_reads_are_exempt_from_ip_rate_limit():
    assert _is_rate_limit_exempt("/mosaicjson/tiles/WebMercatorQuad/8/77/152.png")
    assert not _is_rate_limit_exempt("/mosaicjson/validate")
