import json
import os
from typing import List

from fastapi import APIRouter, HTTPException

from .utils.wrs2_bounds import get_cache_stats, get_tile_bounds_cached, is_tile_cached

router = APIRouter()


@router.get("/create")
async def create_mosaic_json(tile_ids: List[str]):
    """Create mosaic.json dynamically based on requested WRS2 tiles"""

    # Base URL where your COGs are stored (e.g., AWS S3, Google Cloud Storage)
    COG_BASE_URL = os.getenv("COG_STORAGE_URL", "https://your-bucket.s3.amazonaws.com")

    mosaic = {
        "mosaicjson": "0.0.3",
        "version": "1.0.0",
        "minzoom": 8,
        "maxzoom": 14,
        "bounds": [-180, -90, 180, 90],  # Will be calculated dynamically
        "tiles": {},
    }

    # For each WRS2 tile, add to mosaic
    for tile_id in tile_ids:
        cog_url = f"{COG_BASE_URL}/wrs2_tiles/{tile_id}.tif"
        mosaic["tiles"][tile_id] = {
            "url": cog_url,
            "bounds": get_tile_bounds_cached(
                tile_id
            ),  # Implement based on WRS2 geometry
            "minzoom": 8,
            "maxzoom": 14,
        }

    return mosaic


@router.get("/cache/stats")
async def cache_stats():
    """Get statistics about the bounds cache"""
    return get_cache_stats()


def get_tile_bounds(tile_id: str):
    """Get bounds for a WRS2 tile - implement based on your tile naming convention"""
    # This would map tile_id to actual geographic bounds
    # Example: Parse path/row from tile_id
    return [-180, -90, 180, 90]  # Placeholder
