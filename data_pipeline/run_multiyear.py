"""Sequential multi-year Sentinel-2 clear-sky processing."""

from __future__ import annotations

import argparse
import gc
import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path

import geopandas
import numpy as np
import rasterio
import shapely
import xarray as xr
from rasterio.shutil import copy as copy_raster

from data_pipeline.clear_sky import (
    SENSOR_CONFIGS,
    _load_aoi,
    _make_clip_geometry,
    get_jrc_surface_water,
    get_satellite_data,
)


def compute_clear_sky_counts(da_sat: xr.DataArray) -> xr.DataArray:
    """Return clear and valid observation counts as a two-band array."""
    if len(da_sat.time) == 0:
        raise ValueError("Cannot compute clear-sky counts from empty data")

    valid = da_sat.notnull()
    nodata = da_sat.attrs.get("nodata")
    if nodata is not None:
        valid = valid & (da_sat != nodata)

    clear = da_sat.isin(da_sat.attrs["clear_sky_flags"]) & valid
    counts = xr.concat(
        [clear.sum("time"), valid.sum("time")],
        dim=xr.IndexVariable("band", [1, 2]),
    ).astype("uint16")
    counts.attrs.update(da_sat.attrs)
    counts.attrs["band_1"] = "clear_count"
    counts.attrs["band_2"] = "valid_count"
    return counts.rio.write_crs(da_sat.rio.crs)


def store_count_cog(counts: xr.DataArray, output: Path, buffer: int) -> None:
    """Clip and store one year's clear/valid count bands."""
    raster_crs = counts.rio.crs
    if raster_crs is None:
        raise ValueError("Clear-sky count data has no spatial CRS")

    aoi = geopandas.GeoDataFrame(
        geometry=[shapely.from_wkt(counts.attrs["aoi_wkt"])],
        crs=counts.attrs["aoi_crs"],
    )
    clip_geometry = _make_clip_geometry(aoi, raster_crs, buffer)
    clipped = counts.rio.clip([clip_geometry], raster_crs, drop=True)
    clipped.rio.to_raster(output, driver="COG", dtype="uint16", compress="DEFLATE")


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
                nodata=0,
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
            )
            temporary_path.replace(output)
        finally:
            temporary_path.unlink(missing_ok=True)


def run_sequential_multiyear(
    tile_id: str,
    start_year: int,
    end_year: int,
    output: Path,
    work_dir: Path,
    mask_water: bool = True,
    buffer: int = -500,
    chunks: dict[str, int] | None = None,
) -> Path:
    """Process each calendar year independently, then merge observation counts."""
    if end_year < start_year:
        raise ValueError("end_year must be greater than or equal to start_year")

    chunks = chunks or {"x": 1024, "y": 1024}
    aoi = _load_aoi("sentinel2", None, None, tile_id, None)
    work_dir.mkdir(parents=True, exist_ok=True)
    count_paths: list[Path] = []

    for year in range(start_year, end_year + 1):
        logging.info("Processing Sentinel-2 tile %s for %s", tile_id, year)
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
        count_path = work_dir / f"sentinel2_{tile_id.lstrip('T')}_{year}_counts.tif"
        store_count_cog(counts, count_path, buffer)
        count_paths.append(count_path)
        del counts, data
        gc.collect()

    merge_count_cogs(count_paths, output)
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
