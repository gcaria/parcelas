"""Tests for the environment-driven Cloud Run pipeline entry point."""

from pathlib import Path
from unittest.mock import patch

from data_pipeline.run_cloud_job import run_from_env


def test_runs_multiyear_job_and_uploads_result():
    environment = {
        "PIPELINE_MODE": "multiyear",
        "TILE_ID": "T19HCD",
        "START_YEAR": "2020",
        "END_YEAR": "2024",
        "MASK_WATER": "true",
        "BUFFER": "-500",
        "OUTPUT_GCS_PREFIX": "gs://bucket/results",
        "CHECKPOINT_PREFIX": "gs://bucket/checkpoints/tile",
    }
    with (
        patch.dict("os.environ", environment, clear=True),
        patch("data_pipeline.run_cloud_job.run_sequential_multiyear") as run,
        patch(
            "data_pipeline.run_cloud_job._upload",
            return_value="gs://bucket/results/result.tif",
        ) as upload,
    ):
        result = run_from_env()

    assert result == "gs://bucket/results/result.tif"
    assert run.call_args.kwargs["tile_id"] == "T19HCD"
    assert run.call_args.kwargs["checkpoint_prefix"] == ("gs://bucket/checkpoints/tile")
    assert run.call_args.kwargs["mask_water"] is True
    assert isinstance(upload.call_args.args[0], Path)


def test_runs_single_tile_job():
    environment = {
        "PIPELINE_MODE": "tile",
        "SENSOR": "sentinel2",
        "TILE_ID": "T19HCD",
        "TIME_RANGE": "2020-01-01/2020-12-31",
        "OUTPUT_GCS_PREFIX": "gs://bucket/results",
    }
    with (
        patch.dict("os.environ", environment, clear=True),
        patch(
            "data_pipeline.run_cloud_job.run_clear_sky_pipeline",
            return_value="/tmp/result.tif",
        ) as run,
        patch(
            "data_pipeline.run_cloud_job._upload",
            return_value="gs://bucket/results/result.tif",
        ),
    ):
        result = run_from_env()

    assert result == "gs://bucket/results/result.tif"
    assert run.call_args.kwargs["sensor"] == "sentinel2"
    assert run.call_args.kwargs["chunks"] == {"x": 1024, "y": 1024}
