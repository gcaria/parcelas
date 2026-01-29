# api/utils/wrs2_bounds.py
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


def get_bounds_file_path() -> Path:
    """
    Get the absolute path to wrs2_bounds.json.
    Based on your tree structure: api/data/wrs2_bounds.json
    """
    # Get directory where this script (wrs2_bounds.py) is located
    current_file = Path(__file__).resolve()  # /path/to/api/utils/wrs2_bounds.py

    # Go up one level to api/utils, then up to api, then to api/data
    api_dir = current_file.parent.parent  # /path/to/api
    bounds_file = (
        api_dir / "data" / "wrs2_bounds.json"
    )  # /path/to/api/data/wrs2_bounds.json

    return bounds_file


def load_wrs2_bounds() -> Dict:
    """Load pre-computed WRS2 bounds from JSON file"""
    bounds_file = get_bounds_file_path()

    print(f"Looking for bounds file at: {bounds_file}")

    if not bounds_file.exists():
        print(f"❌ WRS2 bounds file not found at: {bounds_file}")
        print(f"   Expected location: {bounds_file}")
        print(f"   Current working directory: {Path.cwd()}")
        print(f"   API directory exists: {(bounds_file.parent.parent).exists()}")
        print(f"   Data directory exists: {(bounds_file.parent).exists()}")
        return {}

    try:
        with open(bounds_file) as f:
            data = json.load(f)
            print(f"✅ Loaded {len(data)} WRS2 bounds from {bounds_file}")
            return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"❌ Error loading {bounds_file}: {e}")
        return {}


# Load bounds once at module level
_WRS2_BOUNDS = None


def _get_wrs2_bounds() -> Dict:
    """Lazy load bounds (avoids loading if never used)"""
    global _WRS2_BOUNDS
    if _WRS2_BOUNDS is None:
        _WRS2_BOUNDS = load_wrs2_bounds()
    return _WRS2_BOUNDS


def get_tile_bounds_cached(tile_id: str) -> List[float]:
    """Get bounds from pre-computed file with fallback"""
    print(tile_id)
    bounds = _get_wrs2_bounds()
    return bounds.get(tile_id, {}).get("bounds", [-180.0, -90.0, 180.0, 90.0])


def get_tile_metadata(tile_id: str) -> Dict:
    """Get all metadata for a tile"""
    bounds = _get_wrs2_bounds()
    return bounds.get(tile_id, {})


def is_tile_cached(tile_id: str) -> bool:
    """Check if tile bounds are in cache"""
    return tile_id in _get_wrs2_bounds()


def get_cache_stats() -> Dict:
    """Get statistics about the bounds cache"""
    bounds = _get_wrs2_bounds()
    bounds_file = get_bounds_file_path()

    stats = {
        "total_tiles_cached": len(bounds),
        "bounds_file_path": str(bounds_file),
        "bounds_file_exists": bounds_file.exists(),
        "bounds_file_readable": (
            os.access(bounds_file, os.R_OK) if bounds_file.exists() else False
        ),
    }

    if bounds_file.exists():
        try:
            file_size = bounds_file.stat().st_size
            stats.update(
                {
                    "bounds_file_size_bytes": file_size,
                    "bounds_file_size_kb": file_size / 1024,
                    "bounds_file_size_mb": file_size / (1024 * 1024),
                }
            )
        except OSError:
            pass

    # Add sample of cached tiles (first 5)
    tile_ids = list(bounds.keys())
    stats["sample_tiles"] = tile_ids[:5] if tile_ids else []

    return stats


def reload_bounds_cache() -> Dict:
    """Force reload of bounds cache (useful for development)"""
    global _WRS2_BOUNDS
    _WRS2_BOUNDS = load_wrs2_bounds()
    return _WRS2_BOUNDS


def get_all_tile_ids() -> List[str]:
    """Get all cached tile IDs"""
    return list(_get_wrs2_bounds().keys())
