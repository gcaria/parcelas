"""Environment-driven entry point for on-demand Cloud Run pipeline jobs."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import cast

import gcsfs

from data_pipeline.clear_sky import Sensor, run_clear_sky_pipeline
from data_pipeline.run_multiyear import run_sequential_multiyear


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _boolean(name: str, default: bool = True) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


def _upload(local_path: Path, output_prefix: str) -> str:
    output_url = f"{output_prefix.rstrip('/')}/{local_path.name}"
    gcsfs.GCSFileSystem().put(str(local_path), output_url)
    return output_url


def run_from_env() -> str:
    """Run a tile or multi-year job from Cloud Run execution overrides."""
    mode = os.environ.get("PIPELINE_MODE", "multiyear")
    output_prefix = _required("OUTPUT_GCS_PREFIX")
    mask_water = _boolean("MASK_WATER")
    buffer = int(os.environ.get("BUFFER", "-500"))

    with tempfile.TemporaryDirectory(prefix="parcelas-pipeline-") as temporary:
        work_dir = Path(temporary)
        if mode == "multiyear":
            tile_id = _required("TILE_ID")
            start_year = int(_required("START_YEAR"))
            end_year = int(_required("END_YEAR"))
            output = work_dir / f"sentinel2_{tile_id}_{start_year}_{end_year}_uint8.tif"
            run_sequential_multiyear(
                tile_id=tile_id,
                start_year=start_year,
                end_year=end_year,
                output=output,
                work_dir=work_dir / "counts",
                mask_water=mask_water,
                buffer=buffer,
                checkpoint_prefix=os.environ.get("CHECKPOINT_PREFIX"),
            )
        elif mode == "tile":
            sensor_value = os.environ.get("SENSOR", "sentinel2")
            if sensor_value not in {"landsat", "sentinel2"}:
                raise ValueError("SENSOR must be 'landsat' or 'sentinel2'")
            sensor = cast(Sensor, sensor_value)
            optional_tile_id = os.environ.get("TILE_ID")
            path = int(os.environ["WRS_PATH"]) if os.environ.get("WRS_PATH") else None
            row = int(os.environ["WRS_ROW"]) if os.environ.get("WRS_ROW") else None
            output_template = str(work_dir / "{tile_key}_uint8.tif")
            output = Path(
                run_clear_sky_pipeline(
                    sensor=sensor,
                    tile_id=optional_tile_id,
                    path=path,
                    row=row,
                    time_range=os.environ.get("TIME_RANGE", "2020-01-01/2020-12-31"),
                    mask_water=mask_water,
                    buffer=buffer,
                    chunks={"x": 1024, "y": 1024},
                    output_template=output_template,
                )
            )
        else:
            raise ValueError("PIPELINE_MODE must be 'tile' or 'multiyear'")

        output_url = _upload(output, output_prefix)
        logging.info("Cloud pipeline output: %s", output_url)
        print(json.dumps({"output_url": output_url}))
        return output_url


def main() -> int:
    """Run the environment-configured Cloud Run pipeline job."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run_from_env()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
