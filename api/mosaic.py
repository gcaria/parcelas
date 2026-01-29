import json
import os
from typing import List

from fastapi import APIRouter, HTTPException, Query

from .utils.wrs2_bounds import get_cache_stats, get_tile_bounds_cached, is_tile_cached

router = APIRouter()


@router.get("/create")
async def create_mosaic_json(
    tile_ids: str = Query(
        ...,
        description="Comma-separated list of WRS2 tile IDs",
        example="233_087,233_088,001_001",
    )
):
    """Create mosaic.json dynamically based on requested WRS2 tiles"""

    COG_BASE_URL = os.getenv("COG_STORAGE_URL", "").rstrip("/")

    if not COG_BASE_URL:
        return {"error": "COG_STORAGE_URL not configured"}

    if not tile_ids:
        raise HTTPException(status_code=400, detail="No tile_ids provided")

    mosaic = {
        "mosaicjson": "0.0.3",
        "version": "1.0.0",
        "minzoom": 8,
        "maxzoom": 14,
        "bounds": [-180, -90, 180, 90],  # Will be calculated dynamically
        "tiles": {},
    }

    # separate comma-separated tile IDs
    tile_ids = [tile_id.strip() for tile_id in tile_ids.split(",")]
    # For each WRS2 tile, add to mosaic
    for tile_id in tile_ids:
        cog_url = f"{COG_BASE_URL}/wrs2/{tile_id}.tif"
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


@router.get("/test")
async def test():
    return {"message": "WRS2 API is working", "test": "success"}
