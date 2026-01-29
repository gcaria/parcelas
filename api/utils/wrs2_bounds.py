# api/utils/wrs2_bounds.py
import json
from pathlib import Path
from typing import List, Dict
import os


def load_wrs2_bounds() -> Dict:
    """Load pre-computed WRS2 bounds from JSON file"""
    # For Vercel, check multiple possible locations
    possible_paths = [
        Path(__file__).parent.parent / "data" / "wrs2_bounds.json",
        Path("/var/task/data/wrs2_bounds.json"),  # Vercel lambda path
        Path("./data/wrs2_bounds.json"),
    ]

    for bounds_file in possible_paths:
        if bounds_file.exists():
            try:
                with open(bounds_file) as f:
                    print(f"Loaded WRS2 bounds from {bounds_file}")
                    return json.load(f)
            except json.JSONDecodeError as e:
                print(f"Error parsing {bounds_file}: {e}")
                continue

    # If no file found, return empty and log warning
    print("Warning: wrs2_bounds.json not found in any expected location")
    return {}


# Load once at module level (cached)
WRS2_BOUNDS = load_wrs2_bounds()


def get_tile_bounds_cached(tile_id: str) -> List[float]:
    """Get bounds from pre-computed file with fallback"""
    return WRS2_BOUNDS.get(tile_id, {}).get("bounds", [-180.0, -90.0, 180.0, 90.0])


def is_tile_cached(tile_id: str) -> bool:
    """Check if tile bounds are in cache"""
    return tile_id in WRS2_BOUNDS


def get_cache_stats() -> Dict:
    """Get statistics about the bounds cache"""
    return {
        "total_tiles_cached": len(WRS2_BOUNDS),
        "cache_size_kb": len(json.dumps(WRS2_BOUNDS).encode("utf-8")) / 1024,
    }
