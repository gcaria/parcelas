import gzip
import json
import os
import time
from collections import defaultdict
from typing import Optional

import gcsfs
from cogeo_mosaic.backends import MosaicBackend
from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from titiler.core.factory import TilerFactory
from titiler.mosaic.factory import MosaicTilerFactory

os.environ["GS_NO_SIGN_REQUEST"] = "YES"
fs = gcsfs.GCSFileSystem()

app = FastAPI(title="WRS2 Mosaic Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "https://gcaria.github.io"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# Simple in-memory rate limiter
rate_limit_storage = defaultdict(list)
RATE_LIMIT = 100  # requests
RATE_WINDOW = 60  # seconds


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()

    # Clean old requests
    rate_limit_storage[client_ip] = [
        req_time
        for req_time in rate_limit_storage[client_ip]
        if now - req_time < RATE_WINDOW
    ]

    # Check rate limit
    if len(rate_limit_storage[client_ip]) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Record request
    rate_limit_storage[client_ip].append(now)

    return await call_next(request)


@app.post("/mosaicjson/generate")
def generate_mosaic(
    tile_ids: Optional[str] = None,
    save_to_gcs: bool = False,
    gcs_path: Optional[str] = None,
):

    COG_BASE_URL = os.getenv("COG_STORAGE_URL", "").rstrip("/")
    if not COG_BASE_URL:
        return {"error": "COG_STORAGE_URL not configured"}

    if not tile_ids:
        files = fs.glob(f"{COG_BASE_URL}/*_uint8.tif")
        cog_urls = [f"gs://{f}" for f in files]
    else:
        tile_ids_list = [t.strip() for t in tile_ids.split(",")]
        cog_urls = [f"{COG_BASE_URL}/{t}_uint8.tif" for t in tile_ids_list]

    mosaic_json = MosaicJSON.from_urls(cog_urls)

    if save_to_gcs:
        if not gcs_path:
            gcs_path = f"{COG_BASE_URL}/mosaics/mosaic_uint8.json.gz"

        json_str = mosaic_json.model_dump_json(indent=2)
        compressed_data = gzip.compress(json_str.encode("utf-8"))

        # Write to GCS with proper error handling
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

    # Return as dict
    return mosaic_json.model_dump()


@app.get("/mosaicjson/validate")
def validate_mosaic(gcs_path: str):
    """Validate a mosaic JSON file"""
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


@app.get("/health")
def health():
    return {"status": "ok"}
