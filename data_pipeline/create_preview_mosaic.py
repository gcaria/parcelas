"""Create a single-COG mosaic and store it locally or on GCS."""

from __future__ import annotations

import argparse
import gzip
from collections.abc import Sequence

import fsspec
from cogeo_mosaic.mosaic import MosaicJSON


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cog-url", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Create and write a compressed MosaicJSON for one COG."""
    args = build_parser().parse_args(argv)
    mosaic = MosaicJSON.from_urls([args.cog_url])
    payload = gzip.compress(mosaic.model_dump_json(indent=2).encode())

    with fsspec.open(args.output, "wb") as destination:
        destination.write(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
