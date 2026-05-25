"""Command-line entry point for running a clear-sky pipeline job for one tile."""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from typing import Any

from data_pipeline.clear_sky import run_clear_sky_pipeline

DEFAULT_TIME_RANGE = "2020-01-01/2020-12-31"
DEFAULT_OUTPUT_TEMPLATE = "gs://my-bucket/cogs/{tile_key}_uint8.tif"


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Run clear-sky processing for one Landsat or Sentinel-2 tile."
    )
    parser.add_argument(
        "--sensor",
        choices=["landsat", "sentinel2"],
        default="landsat",
        help="Satellite sensor to process.",
    )
    parser.add_argument("--path", type=int, help="Landsat WRS-2 path.")
    parser.add_argument("--row", type=int, help="Landsat WRS-2 row.")
    parser.add_argument(
        "--tile-id",
        help="Sentinel-2 MGRS tile ID, such as T19HCD or 19HCD.",
    )
    parser.add_argument(
        "--aoi-geojson",
        help=(
            "AOI GeoJSON path readable by GeoPandas. Optional for both sensors: "
            "Landsat defaults to the WRS-2 tile geometry and Sentinel-2 defaults to "
            "the MGRS tile geometry when this flag is omitted."
        ),
    )
    parser.add_argument(
        "--time-range",
        default=DEFAULT_TIME_RANGE,
        help="STAC datetime range, formatted as YYYY-MM-DD/YYYY-MM-DD.",
    )
    parser.add_argument(
        "--output-template",
        default=DEFAULT_OUTPUT_TEMPLATE,
        help="Output path template. Supports {tile_key}, {sensor}, {path}, {row}, and {tile_id}.",
    )
    parser.add_argument(
        "--buffer",
        type=int,
        default=-500,
        help="Clip geometry buffer in meters.",
    )
    parser.add_argument(
        "--chunk-x",
        type=int,
        default=512,
        help="Dask chunk size for the x dimension.",
    )
    parser.add_argument(
        "--chunk-y",
        type=int,
        default=512,
        help="Dask chunk size for the y dimension.",
    )
    parser.add_argument(
        "--no-mask-water",
        action="store_true",
        help="Disable JRC surface-water masking.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Python logging level.",
    )
    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Validate sensor-specific arguments."""
    if args.sensor == "landsat" and (args.path is None or args.row is None):
        parser.error("--path and --row are required when --sensor=landsat")

    if args.sensor == "sentinel2" and not args.tile_id:
        parser.error("--tile-id is required when --sensor=sentinel2")


def connect_dask_from_env() -> Any | None:
    """Connect to a Dask scheduler when DASK_SCHEDULER_ADDRESS is set."""
    scheduler_address = os.environ.get("DASK_SCHEDULER_ADDRESS")
    if not scheduler_address:
        logging.info("DASK_SCHEDULER_ADDRESS is not set; using local Dask execution")
        return None

    from distributed import Client

    logging.info("Connecting to Dask scheduler at %s", scheduler_address)
    return Client(scheduler_address)


def run_from_args(args: argparse.Namespace) -> str:
    """Run the pipeline from parsed CLI arguments."""
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    client = connect_dask_from_env()
    try:
        output_path = run_clear_sky_pipeline(
            path=args.path,
            row=args.row,
            tile_id=args.tile_id,
            sensor=args.sensor,
            aoi_geojson=args.aoi_geojson,
            time_range=args.time_range,
            bands=None,
            chunks={"x": args.chunk_x, "y": args.chunk_y},
            mask_water=not args.no_mask_water,
            output_template=args.output_template,
            buffer=args.buffer,
        )
        logging.info("Pipeline completed: %s", output_path)
        return output_path
    finally:
        if client is not None:
            client.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)
    run_from_args(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
