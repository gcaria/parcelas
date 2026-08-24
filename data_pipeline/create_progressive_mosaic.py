"""Build a mosaic from the newest completed preview for every Sentinel-2 tile."""

from __future__ import annotations

import argparse
import gzip
import re
from collections.abc import Iterable, Sequence

import fsspec
from cogeo_mosaic.mosaic import MosaicJSON

PREVIEW_COG_RE = re.compile(
    r"(?:^|/)run-(?P<run_id>\d+)/sentinel2_(?P<tile>[0-9A-Z]{5})_uint8\.tif$"
)


def select_latest_cogs(paths: Iterable[str]) -> list[str]:
    """Select the COG from the greatest workflow run ID for each MGRS tile."""
    latest: dict[str, tuple[int, str]] = {}
    for path in paths:
        normalized = path.removeprefix("gs://")
        match = PREVIEW_COG_RE.search(normalized)
        if not match:
            continue

        tile = match.group("tile")
        candidate = (int(match.group("run_id")), f"gs://{normalized}")
        if tile not in latest or candidate[0] > latest[tile][0]:
            latest[tile] = candidate

    return [latest[tile][1] for tile in sorted(latest)]


def select_all_cogs(paths: Iterable[str]) -> list[str]:
    """Return every matching COG as a normalized, sorted GCS URL."""
    return sorted({f"gs://{path.removeprefix('gs://')}" for path in paths})


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-pattern",
        default="gs://parcelas-wrs2/previews/run-*/sentinel2_*_uint8.tif",
    )
    parser.add_argument(
        "--all-matches",
        action="store_true",
        help="Include every COG matching the input pattern without run-ID filtering.",
    )
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Discover completed COGs and write their compressed MosaicJSON."""
    args = build_parser().parse_args(argv)
    filesystem, glob_path = fsspec.core.url_to_fs(args.input_pattern)
    paths = filesystem.glob(glob_path)
    cog_urls = select_all_cogs(paths) if args.all_matches else select_latest_cogs(paths)
    if not cog_urls:
        raise RuntimeError(f"No Sentinel-2 preview COGs match {args.input_pattern}")

    mosaic = MosaicJSON.from_urls(cog_urls)
    payload = gzip.compress(mosaic.model_dump_json(indent=2).encode())
    with fsspec.open(args.output, "wb") as destination:
        destination.write(payload)

    print(f"Published {len(cog_urls)} tiles to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
