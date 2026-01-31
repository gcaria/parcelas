import os

from cogeo_mosaic.backends import MosaicBackend
from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from titiler.core.factory import TilerFactory
from titiler.mosaic.factory import MosaicTilerFactory

os.environ["GS_NO_SIGN_REQUEST"] = "YES"

app = FastAPI(title="WRS2 Mosaic Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:3000"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.post("/mosaicjson/generate")
def generate_mosaic(tile_ids: str):
    COG_BASE_URL = os.getenv("COG_STORAGE_URL", "").rstrip("/")
    if not COG_BASE_URL:
        return {"error": "COG_STORAGE_URL not configured"}
    if not tile_ids:
        raise HTTPException(status_code=400, detail="No tile_ids provided")

    tile_ids_list = [t.strip() for t in tile_ids.split(",")]
    cog_urls = [f"{COG_BASE_URL}/{tile_id}.tif" for tile_id in tile_ids_list]

    # Generate and return the full mosaic JSON
    mosaic_json = MosaicJSON.from_urls(cog_urls)

    # Return as dict
    return mosaic_json.model_dump()


mosaic = MosaicTilerFactory(backend=MosaicBackend, router_prefix="/mosaicjson")
app.include_router(mosaic.router, prefix="/mosaicjson")


@app.get("/health")
def health():
    return {"status": "ok"}
