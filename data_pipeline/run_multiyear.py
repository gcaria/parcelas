"""Sequential multi-year Sentinel-2 clear-sky processing."""

from __future__ import annotations

import argparse
import gc
import json
import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path

import gcsfs
import geopandas
import numpy as np
import rasterio
import shapely
import xarray as xr
from planetary_computer.sas import TOKEN_CACHE
from rasterio.shutil import copy as copy_raster

from data_pipeline.clear_sky import (
    SENSOR_CONFIGS,
    _daily_clear_and_valid,
    _load_aoi,
    _make_clip_geometry,
    get_jrc_surface_water,
    get_satellite_data,
)

CHECKPOINT_VERSION = 2


def compute_clear_sky_counts(da_sat: xr.DataArray) -> xr.DataArray:
    """Return clear and valid observation counts as a two-band array."""
    if len(da_sat.time) == 0:
        raise ValueError("Cannot compute clear-sky counts from empty data")

    clear, valid = _daily_clear_and_valid(da_sat, da_sat.attrs["clear_sky_flags"])
    counts = xr.concat(
        [clear.sum("day"), valid.sum("day")],
        dim=xr.IndexVariable("band", [1, 2]),
    ).astype("uint16")
    counts.attrs.update(da_sat.attrs)
    counts.attrs["band_1"] = "clear_count"
    counts.attrs["band_2"] = "valid_count"
    return counts.rio.write_crs(da_sat.rio.crs)


def compute_acquisition_counts(da_sat: xr.DataArray) -> xr.DataArray:
    """Return pre-deduplication clear and valid acquisition counts."""
    if len(da_sat.time) == 0:
        raise ValueError("Cannot compute clear-sky counts from empty data")

    nodata = da_sat.attrs.get("nodata", 0)
    valid = da_sat.notnull() & (da_sat != nodata)
    clear = da_sat.isin(da_sat.attrs["clear_sky_flags"]) & valid
    counts = xr.concat(
        [clear.sum("time"), valid.sum("time")],
        dim=xr.IndexVariable("band", [1, 2]),
    ).astype("uint16")
    counts.attrs.update(da_sat.attrs)
    counts.attrs["band_1"] = "clear_count"
    counts.attrs["band_2"] = "valid_count"
    return counts.rio.write_crs(da_sat.rio.crs)


def accumulate_counts(
    counts: xr.DataArray,
    accumulator: Path,
    buffer: int,
    *,
    reset: bool = False,
) -> None:
    """Add one year's counts to a windowed two-band on-disk accumulator."""
    raster_crs = counts.rio.crs
    if raster_crs is None:
        raise ValueError("Clear-sky count data has no spatial CRS")

    aoi = geopandas.GeoDataFrame(
        geometry=[shapely.from_wkt(counts.attrs["aoi_wkt"])],
        crs=counts.attrs["aoi_crs"],
    )
    clip_geometry = _make_clip_geometry(aoi, raster_crs, buffer)
    clipped = counts.rio.clip([clip_geometry], raster_crs, drop=True)
    accumulator.parent.mkdir(parents=True, exist_ok=True)

    create = reset or not accumulator.exists()
    if create:
        profile = {
            "driver": "GTiff",
            "width": clipped.rio.width,
            "height": clipped.rio.height,
            "count": 2,
            "dtype": "uint32",
            "crs": raster_crs,
            "transform": clipped.rio.transform(),
            "nodata": 0,
            "tiled": True,
            "blockxsize": 1024,
            "blockysize": 1024,
            "BIGTIFF": "IF_SAFER",
        }
        with rasterio.open(accumulator, "w", **profile):
            pass

    with rasterio.open(accumulator, "r+") as destination:
        actual = (
            destination.crs,
            destination.transform,
            destination.width,
            destination.height,
        )
        expected = (
            raster_crs,
            clipped.rio.transform(),
            clipped.rio.width,
            clipped.rio.height,
        )
        if destination.count != 2 or actual != expected:
            raise ValueError("Yearly counts do not align with the accumulator")

        for _, window in destination.block_windows(1):
            y_slice = slice(int(window.row_off), int(window.row_off + window.height))
            x_slice = slice(int(window.col_off), int(window.col_off + window.width))
            yearly = (
                clipped.isel(y=y_slice, x=x_slice)
                .compute()
                .values.astype("uint32", copy=False)
            )
            if not create:
                yearly = yearly + destination.read(window=window, out_dtype="uint32")
            destination.write(yearly, window=window)


def replace_counts(
    old_counts: xr.DataArray,
    new_counts: xr.DataArray,
    accumulator: Path,
    buffer: int,
) -> None:
    """Replace one year's contribution in an existing count accumulator."""
    if not accumulator.exists():
        raise FileNotFoundError(f"Count accumulator does not exist: {accumulator}")
    if old_counts.rio.crs != new_counts.rio.crs:
        raise ValueError("Old and new yearly counts have different CRSs")

    aoi = geopandas.GeoDataFrame(
        geometry=[shapely.from_wkt(new_counts.attrs["aoi_wkt"])],
        crs=new_counts.attrs["aoi_crs"],
    )
    raster_crs = new_counts.rio.crs
    clip_geometry = _make_clip_geometry(aoi, raster_crs, buffer)
    old_clipped = old_counts.rio.clip([clip_geometry], raster_crs, drop=True)
    new_clipped = new_counts.rio.clip([clip_geometry], raster_crs, drop=True)

    with rasterio.open(accumulator, "r+") as destination:
        expected = (
            destination.crs,
            destination.transform,
            destination.width,
            destination.height,
        )
        for counts in (old_clipped, new_clipped):
            actual = (
                counts.rio.crs,
                counts.rio.transform(),
                counts.rio.width,
                counts.rio.height,
            )
            if counts.sizes.get("band") != 2 or actual != expected:
                raise ValueError("Replacement counts do not align with the accumulator")

        for _, window in destination.block_windows(1):
            y_slice = slice(int(window.row_off), int(window.row_off + window.height))
            x_slice = slice(int(window.col_off), int(window.col_off + window.width))
            old = (
                old_clipped.isel(y=y_slice, x=x_slice).compute().values.astype("uint32")
            )
            new = (
                new_clipped.isel(y=y_slice, x=x_slice).compute().values.astype("uint32")
            )
            aggregate = destination.read(window=window, out_dtype="uint32")
            if np.any(old > aggregate):
                raise ValueError("Old yearly counts exceed the five-year accumulator")
            replacement = aggregate - old + new
            destination.write(replacement, window=window)


def finalize_count_accumulator(accumulator: Path, output: Path) -> None:
    """Convert a two-band count accumulator to a clear-sky percentage COG."""
    with rasterio.open(accumulator) as source:
        if source.count != 2:
            raise ValueError("Count accumulator must contain exactly two bands")

        profile = source.profile.copy()
        profile.update(
            driver="GTiff", count=1, dtype="uint8", nodata=0, compress="DEFLATE"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".tif", prefix="clear-sky-finalize-", dir=output.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)

        try:
            with rasterio.open(temporary_path, "w", **profile) as destination:
                for _, window in source.block_windows(1):
                    clear_count = source.read(1, window=window, out_dtype="uint32")
                    valid_count = source.read(2, window=window, out_dtype="uint32")
                    percentage = np.zeros_like(clear_count, dtype="uint8")
                    np.divide(
                        clear_count * 100,
                        valid_count,
                        out=percentage,
                        where=valid_count > 0,
                        casting="unsafe",
                    )
                    destination.write(percentage, 1, window=window)

            copy_raster(
                temporary_path,
                output,
                driver="COG",
                compress="DEFLATE",
                NUM_THREADS="ALL_CPUS",
            )
        finally:
            temporary_path.unlink(missing_ok=True)


def merge_count_cogs(inputs: Sequence[Path], output: Path) -> None:
    """Window-sum annual count COGs and write a uint8 percentage COG."""
    if not inputs:
        raise ValueError("At least one count COG is required")

    sources = [rasterio.open(path) for path in inputs]
    try:
        reference = sources[0]
        expected = (
            reference.crs,
            reference.transform,
            reference.width,
            reference.height,
        )
        for source in sources:
            actual = (source.crs, source.transform, source.width, source.height)
            if source.count != 2 or actual != expected:
                raise ValueError("Annual count COGs are not aligned two-band rasters")

        profile = reference.profile.copy()
        profile.update(
            driver="GTiff", count=1, dtype="uint8", nodata=0, compress="DEFLATE"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            suffix=".tif", prefix="clear-sky-merge-", dir=output.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)

        try:
            with rasterio.open(temporary_path, "w", **profile) as destination:
                for _, window in reference.block_windows(1):
                    clear_count = np.zeros(
                        (window.height, window.width), dtype="uint32"
                    )
                    valid_count = np.zeros_like(clear_count)
                    for source in sources:
                        clear_count += source.read(1, window=window, out_dtype="uint32")
                        valid_count += source.read(2, window=window, out_dtype="uint32")

                    percentage = np.zeros_like(clear_count, dtype="uint8")
                    np.divide(
                        clear_count * 100,
                        valid_count,
                        out=percentage,
                        where=valid_count > 0,
                        casting="unsafe",
                    )
                    destination.write(percentage, 1, window=window)

            copy_raster(
                temporary_path,
                output,
                driver="COG",
                compress="DEFLATE",
                NUM_THREADS="ALL_CPUS",
            )
        finally:
            temporary_path.unlink(missing_ok=True)
    finally:
        for source in sources:
            source.close()


def apply_surface_water_mask(
    output: Path,
    aoi: geopandas.GeoDataFrame,
    chunks: dict[str, int],
) -> None:
    """Apply the time-invariant JRC surface-water mask to a merged result once."""
    with xr.open_dataarray(output, engine="rasterio", chunks=chunks) as result:
        water = get_jrc_surface_water(aoi, chunks=chunks)["occurrence"]
        water = water.rio.reproject_match(result).squeeze(drop=True)
        masked = result.where(water < 90, 0).astype("uint8")
        masked = masked.rio.write_crs(result.rio.crs).rio.write_nodata(0)

        with tempfile.NamedTemporaryFile(
            suffix=".tif",
            prefix="clear-sky-water-mask-",
            dir=output.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        try:
            masked.rio.to_raster(
                temporary_path,
                driver="COG",
                dtype="uint8",
                nodata=0,
                compress="DEFLATE",
                NUM_THREADS="ALL_CPUS",
            )
            temporary_path.replace(output)
        finally:
            temporary_path.unlink(missing_ok=True)


def refresh_planetary_computer_tokens() -> None:
    """Force subsequent STAC assets to receive a fresh SAS token."""
    TOKEN_CACHE.clear()


def restore_checkpoint(
    accumulator: Path,
    checkpoint_prefix: str,
    *,
    tile_id: str,
    start_year: int,
    end_year: int,
    buffer: int,
) -> int | None:
    """Restore a compatible accumulator and return its last completed year."""
    filesystem = gcsfs.GCSFileSystem()
    manifest_url = f"{checkpoint_prefix}.json"
    if not filesystem.exists(manifest_url):
        return None

    with filesystem.open(manifest_url, "r") as manifest_file:
        manifest = json.load(manifest_file)

    expected = {
        "version": CHECKPOINT_VERSION,
        "tile_id": tile_id.upper(),
        "start_year": start_year,
        "end_year": end_year,
        "buffer": buffer,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        logging.warning("Ignoring incompatible checkpoint manifest at %s", manifest_url)
        return None

    checkpoint_url = manifest["accumulator_url"]
    if not filesystem.exists(checkpoint_url):
        logging.warning("Checkpoint raster is missing: %s", checkpoint_url)
        return None

    accumulator.parent.mkdir(parents=True, exist_ok=True)
    filesystem.get(checkpoint_url, str(accumulator))
    completed_year = int(manifest["completed_year"])
    logging.info(
        "Restored checkpoint through %s from %s", completed_year, checkpoint_url
    )
    return completed_year


def save_checkpoint(
    accumulator: Path,
    checkpoint_prefix: str,
    *,
    tile_id: str,
    start_year: int,
    end_year: int,
    completed_year: int,
    buffer: int,
) -> None:
    """Publish an annual accumulator checkpoint, then atomically advance its
    manifest.
    """
    filesystem = gcsfs.GCSFileSystem()
    checkpoint_url = f"{checkpoint_prefix}-through-{completed_year}.tif"
    manifest_url = f"{checkpoint_prefix}.json"
    previous_url = None
    if filesystem.exists(manifest_url):
        with filesystem.open(manifest_url, "r") as previous_manifest_file:
            previous_url = json.load(previous_manifest_file).get("accumulator_url")

    filesystem.put(str(accumulator), checkpoint_url)
    manifest = {
        "version": CHECKPOINT_VERSION,
        "tile_id": tile_id.upper(),
        "start_year": start_year,
        "end_year": end_year,
        "completed_year": completed_year,
        "buffer": buffer,
        "accumulator_url": checkpoint_url,
    }
    with filesystem.open(manifest_url, "w") as manifest_file:
        json.dump(manifest, manifest_file)

    if previous_url and previous_url != checkpoint_url:
        filesystem.rm(previous_url)
    logging.info("Saved checkpoint through %s to %s", completed_year, checkpoint_url)


def run_sequential_multiyear(
    tile_id: str,
    start_year: int,
    end_year: int,
    output: Path,
    work_dir: Path,
    mask_water: bool = True,
    buffer: int = -500,
    chunks: dict[str, int] | None = None,
    checkpoint_prefix: str | None = None,
) -> Path:
    """Process each calendar year independently, then merge observation counts."""
    if end_year < start_year:
        raise ValueError("end_year must be greater than or equal to start_year")

    chunks = chunks or {"x": 1024, "y": 1024}
    aoi = _load_aoi("sentinel2", None, None, tile_id, None)
    work_dir.mkdir(parents=True, exist_ok=True)
    accumulator = work_dir / f"sentinel2_{tile_id.lstrip('T')}_counts.tif"
    completed_year = None
    if checkpoint_prefix:
        completed_year = restore_checkpoint(
            accumulator,
            checkpoint_prefix,
            tile_id=tile_id,
            start_year=start_year,
            end_year=end_year,
            buffer=buffer,
        )

    first_year_to_process = max(start_year, (completed_year or start_year - 1) + 1)
    for year in range(first_year_to_process, end_year + 1):
        logging.info("Processing Sentinel-2 tile %s for %s", tile_id, year)
        refresh_planetary_computer_tokens()
        data = get_satellite_data(
            shp=aoi,
            tile_id=tile_id,
            sensor="sentinel2",
            time_range=f"{year}-01-01/{year}-12-31",
            chunks=chunks,
            # JRC is static, so applying it once after the yearly merge avoids
            # downloading and reprojecting the same mask for every year.
            mask_water=False,
        )
        data.attrs["clear_sky_flags"] = SENSOR_CONFIGS["sentinel2"]["clear_sky_flags"]
        counts = compute_clear_sky_counts(data)
        accumulate_counts(
            counts,
            accumulator,
            buffer,
            reset=year == start_year and completed_year is None,
        )
        del counts, data
        gc.collect()
        if checkpoint_prefix:
            save_checkpoint(
                accumulator,
                checkpoint_prefix,
                tile_id=tile_id,
                start_year=start_year,
                end_year=end_year,
                completed_year=year,
                buffer=buffer,
            )

    finalize_count_accumulator(accumulator, output)
    if mask_water:
        apply_surface_water_mask(output, aoi, chunks)
    return output


def build_parser() -> argparse.ArgumentParser:
    """Build command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-id", required=True)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("annual-counts"))
    parser.add_argument("--buffer", type=int, default=-500)
    parser.add_argument("--chunk-x", type=int, default=1024)
    parser.add_argument("--chunk-y", type=int, default=1024)
    parser.add_argument("--no-mask-water", action="store_true")
    parser.add_argument(
        "--checkpoint-prefix",
        help="Optional GCS prefix used to restore and save annual checkpoints.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run sequential multi-year processing from the command line."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run_sequential_multiyear(
        tile_id=args.tile_id,
        start_year=args.start_year,
        end_year=args.end_year,
        output=args.output,
        work_dir=args.work_dir,
        mask_water=not args.no_mask_water,
        buffer=args.buffer,
        chunks={"x": args.chunk_x, "y": args.chunk_y},
        checkpoint_prefix=args.checkpoint_prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
