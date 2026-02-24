from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app, rate_limit_storage

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


def test_generate_missing_cog_storage_url():
    with patch.dict("os.environ", {"COG_STORAGE_URL": ""}):
        response = client.post(
            "/mosaicjson/generate", headers={"X-API-Key": "test-key"}
        )
        assert response.json() == {"error": "COG_STORAGE_URL not configured"}


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
