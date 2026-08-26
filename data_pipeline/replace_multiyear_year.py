"""Replace one calendar year's contribution in a multi-year count raster."""

from __future__ import annotations

import argparse
import gc
import logging
from collections.abc import Sequence
from pathlib import Path

from data_pipeline.clear_sky import SENSOR_CONFIGS, _load_aoi, get_satellite_data
from data_pipeline.run_multiyear import (
    apply_surface_water_mask,
    compute_acquisition_counts,
    compute_clear_sky_counts,
    finalize_count_accumulator,
    refresh_planetary_computer_tokens,
    replace_counts,
)


def run_year_replacement(
    tile_id: str,
    year: int,
    accumulator: Path,
    output: Path,
    *,
    mask_water: bool = True,
    buffer: int = -500,
    chunks: dict[str, int] | None = None,
) -> Path:
    """Replace legacy acquisition counts for one year with daily counts."""
    chunks = chunks or {"x": 1024, "y": 1024}
    aoi = _load_aoi("sentinel2", None, None, tile_id, None)
    refresh_planetary_computer_tokens()
    data = get_satellite_data(
        shp=aoi,
        tile_id=tile_id,
        sensor="sentinel2",
        time_range=f"{year}-01-01/{year}-12-31",
        chunks=chunks,
        mask_water=False,
    )
    data.attrs["clear_sky_flags"] = SENSOR_CONFIGS["sentinel2"]["clear_sky_flags"]
    old_counts = compute_acquisition_counts(data)
    new_counts = compute_clear_sky_counts(data)
    replace_counts(old_counts, new_counts, accumulator, buffer)
    del old_counts, new_counts, data
    gc.collect()

    finalize_count_accumulator(accumulator, output)
    if mask_water:
        apply_surface_water_mask(output, aoi, chunks)
    return output


def build_parser() -> argparse.ArgumentParser:
    """Build command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-id", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--accumulator", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--buffer", type=int, default=-500)
    parser.add_argument("--chunk-x", type=int, default=1024)
    parser.add_argument("--chunk-y", type=int, default=1024)
    parser.add_argument("--no-mask-water", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a yearly count replacement from the command line."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run_year_replacement(
        tile_id=args.tile_id,
        year=args.year,
        accumulator=args.accumulator,
        output=args.output,
        mask_water=not args.no_mask_water,
        buffer=args.buffer,
        chunks={"x": args.chunk_x, "y": args.chunk_y},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
