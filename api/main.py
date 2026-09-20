import gzip
import json
import logging
import os
import secrets
import time
from collections import defaultdict
from functools import lru_cache
from typing import Optional

import gcsfs
from cogeo_mosaic.backends import MosaicBackend
from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from titiler.mosaic.factory import MosaicTilerFactory

RATE_LIMIT = 100  # requests
RATE_WINDOW = 60  # seconds
API_KEY = os.getenv("API_KEY")
SUPPORTED_SENSORS = {"landsat", "sentinel2"}
PUBLIC_PATHS = {
    "/health",
    "/chirps/precipitation/map",
    "/terraclimate/temperature/map",
    "/mosaicjson/sensors",
    "/mosaicjson/info",
}
PUBLIC_PATH_PREFIXES = ("/mosaicjson/tiles/", "/mosaicjson/point/")
RATE_LIMIT_EXEMPT_PREFIXES = ("/mosaicjson/tiles/", "/mosaicjson/point/")
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3001",  # default for local dev only
).split(",")

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

os.environ["GS_NO_SIGN_REQUEST"] = "YES"

fs = gcsfs.GCSFileSystem()

app = FastAPI(title="WRS2 Mosaic Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Require the administrative API key except on read-only public routes."""

    if _is_public_path(request.url.path):
        return await call_next(request)

    # Check header first, then fall back to query param
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

    if not key or not secrets.compare_digest(key, API_KEY):
        return JSONResponse(
            status_code=401, content={"detail": "Invalid or missing API key"}
        )

    return await call_next(request)


# Simple in-memory rate limiter
rate_limit_storage: dict = defaultdict(list)


def _is_public_path(path: str) -> bool:
    """Return whether a route is safe for unauthenticated frontend reads."""
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES)


def _client_ip(request: Request) -> str:
    """Return the original client IP when running behind a trusted proxy."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip()

    return request.client.host if request.client else "unknown"


def _is_rate_limit_exempt(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(RATE_LIMIT_EXEMPT_PREFIXES)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """
    Rate-limit non-tile API operations by client IP.

    Cloud Run does not reliably expose the original address to the application, so
    limiting browser tile reads here can put every viewer in one shared bucket. Tile
    requests remain protected by the API-key middleware.
    """
    if _is_rate_limit_exempt(request.url.path):
        return await call_next(request)

    client_ip = _client_ip(request)
    now = time.time()

    # Clean old requests
    rate_limit_storage[client_ip] = [
        req_time
        for req_time in rate_limit_storage[client_ip]
        if now - req_time < RATE_WINDOW
    ]
    if len(rate_limit_storage[client_ip]) >= RATE_LIMIT:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    rate_limit_storage[client_ip].append(now)
    return await call_next(request)


@app.post("/mosaicjson/generate")
def generate_mosaic(
    tile_ids: Optional[str] = None,
    save_to_gcs: bool = False,
    gcs_path: Optional[str] = None,
    glob_pattern: str = "uint8",
    sensor: str = "landsat",
):
    """
    Generate a mosaic JSON from COGs in GCS.

    Optionally save the mosaic JSON back to GCS.
    """
    COG_BASE_URL = os.getenv("COG_STORAGE_URL", "").rstrip("/")
    if not COG_BASE_URL:
        return {"error": "COG_STORAGE_URL not configured"}

    if sensor not in SUPPORTED_SENSORS:
        supported_sensors = ", ".join(sorted(SUPPORTED_SENSORS))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported sensor '{sensor}'. Use one of: {supported_sensors}",
        )

    if not tile_ids:
        files = fs.glob(f"{COG_BASE_URL}/{sensor}_*_{glob_pattern}.tif")
        cog_urls = [f"gs://{f}" for f in files]
    else:
        tile_ids_list = [t.strip() for t in tile_ids.split(",")]
        cog_urls = [f"{COG_BASE_URL}/{t}_{glob_pattern}.tif" for t in tile_ids_list]

    logger.info(f"Generating mosaic for COGS: {cog_urls}")
    mosaic_json = MosaicJSON.from_urls(cog_urls)

    if save_to_gcs:
        if not gcs_path:
            gcs_path = f"{COG_BASE_URL}/mosaics/mosaic_{sensor}_{glob_pattern}.json.gz"
        json_str = mosaic_json.model_dump_json(indent=2)
        compressed_data = gzip.compress(json_str.encode("utf-8"))
        try:
            with fs.open(gcs_path, "wb") as f:
                f.write(compressed_data)
            return {
                "status": "success",
                "mosaic": mosaic_json.model_dump(),
                "saved_to": gcs_path,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}")

    return mosaic_json.model_dump()


@app.get("/mosaicjson/sensors")
def list_mosaic_sensors(glob_pattern: str = "uint8"):
    """List supported frontend mosaic sensor options."""
    COG_BASE_URL = os.getenv("COG_STORAGE_URL", "").rstrip("/")
    if not COG_BASE_URL:
        return {"error": "COG_STORAGE_URL not configured"}

    return {
        "sensors": [
            {
                "id": "landsat",
                "label": "Landsat",
                "mosaic_url": f"{COG_BASE_URL}/mosaics/mosaic_landsat_{glob_pattern}.json.gz",
            },
            {
                "id": "sentinel2",
                "label": "Sentinel-2",
                "mosaic_url": f"{COG_BASE_URL}/mosaics/mosaic_sentinel2_{glob_pattern}.json.gz",
            },
        ]
    }


@app.get("/mosaicjson/validate")
def validate_mosaic(gcs_path: str):
    """Validate a mosaic JSON file."""
    try:
        with fs.open(gcs_path, "rb") as f:
            data = f.read()

        # Try to decompress
        if gcs_path.endswith(".gz"):
            decompressed = gzip.decompress(data)
            json_data = json.loads(decompressed)
        else:
            json_data = json.loads(data)

        return {
            "valid": True,
            "file_size": len(data),
            "tiles_count": len(json_data.get("tiles", {})),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


mosaic = MosaicTilerFactory(backend=MosaicBackend, router_prefix="/mosaicjson")
app.include_router(mosaic.router, prefix="/mosaicjson")


@lru_cache(maxsize=1)
def _chirps_precipitation_map() -> dict:
    """Create the CHIRPS v3 map for 2020–2024 mean annual precipitation."""
    import ee

    project = os.getenv("EARTH_ENGINE_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        ee.Initialize(project=project)
    else:
        ee.Initialize()

    precipitation = (
        ee.ImageCollection("UCSB-CHC/CHIRPS/V3/DAILY_RNL")
        .filterDate("2020-01-01", "2025-01-01")
        .select("precipitation")
        .sum()
        .divide(5)
        .rename("mean_annual_precipitation_mm")
    )
    non_water = (
        ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0).lt(90)
    )
    precipitation = precipitation.updateMask(non_water)
    palette = [
        "fff7ec",
        "fee8c8",
        "fdd49e",
        "fdbb84",
        "fc8d59",
        "ef6548",
        "d7301f",
        "b30000",
        "7f0000",
    ]
    map_id = precipitation.getMapId({"min": 0, "max": 3000, "palette": palette})
    return {
        "tile_url": map_id["tile_fetcher"].url_format,
        "min_mm": 0,
        "max_mm": 3000,
        "palette": palette,
        "period": "2020–2024",
    }


@app.get("/chirps/precipitation/map")
def chirps_precipitation_map():
    """Return tiles for 2020–2024 CHIRPS v3 mean annual precipitation."""
    try:
        return _chirps_precipitation_map()
    except Exception as error:
        logger.exception("Unable to create the CHIRPS precipitation map")
        raise HTTPException(
            status_code=503, detail="CHIRPS precipitation layer is unavailable"
        ) from error


@lru_cache(maxsize=1)
def _terraclimate_temperature_map() -> dict:
    """Create the 2020–2024 day-weighted mean air-temperature map."""
    import ee

    project = os.getenv("EARTH_ENGINE_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        ee.Initialize(project=project)
    else:
        ee.Initialize()

    collection = ee.ImageCollection("IDAHO_EPSCOR/TERRACLIMATE").filterDate(
        "2020-01-01", "2025-01-01"
    )

    def weighted_month(image):
        date = ee.Date(image.get("system:time_start"))
        days = date.advance(1, "month").difference(date, "day")
        mean_temperature = (
            image.select(["tmmn", "tmmx"]).reduce(ee.Reducer.mean()).multiply(0.1)
        )
        return mean_temperature.multiply(days)

    temperature = (
        collection.map(weighted_month)
        .sum()
        .divide(1827)
        .rename("mean_air_temperature_c")
    )
    non_water = (
        ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").unmask(0).lt(90)
    )
    temperature = temperature.updateMask(non_water)
    palette = [
        "313695",
        "4575b4",
        "74add1",
        "abd9e9",
        "e0f3f8",
        "ffffbf",
        "fee090",
        "fdae61",
        "f46d43",
        "d73027",
        "a50026",
    ]
    map_id = temperature.getMapId({"min": -5, "max": 25, "palette": palette})
    return {
        "tile_url": map_id["tile_fetcher"].url_format,
        "min_c": -5,
        "max_c": 25,
        "palette": palette,
        "period": "2020–2024",
    }


@app.get("/terraclimate/temperature/map")
def terraclimate_temperature_map():
    """Return tiles for 2020–2024 TerraClimate mean air temperature."""
    try:
        return _terraclimate_temperature_map()
    except Exception as error:
        logger.exception("Unable to create the TerraClimate temperature map")
        raise HTTPException(
            status_code=503, detail="TerraClimate temperature layer is unavailable"
        ) from error


@app.get("/health")
def health():
    """Simple health check endpoint."""
    return {"status": "ok"}
