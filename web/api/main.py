from titiler.application import TilerFactory
from titiler.mosaic.factory import MosaicTilerFactory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

# Initialize app
app = FastAPI(title="WRS2 COG Mosaic Server")

# Add CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# COG endpoints
cog = TilerFactory()
app.include_router(cog.router, prefix="/cog", tags=["Cloud Optimized GeoTIFF"])

# Mosaic endpoints
mosaic = MosaicTilerFactory()
app.include_router(mosaic.router, prefix="/mosaic", tags=["Mosaic"])


@app.get("/health")
def health():
    return {"status": "healthy"}
