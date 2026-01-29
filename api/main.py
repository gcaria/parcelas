from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from titiler.core.factory import TilerFactory
from titiler.mosaic.factory import MosaicTilerFactory

from .mosaic import router as wrs2_router  # Import your custom router

app = FastAPI(title="WRS2 COG Mosaic Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. Create the COG endpoints (for single TIFs)
cog = TilerFactory()
app.include_router(cog.router, prefix="/cog", tags=["Single COG"])

# 2. Create the Mosaic endpoints (for WRS2 mosaicJSON)
mosaic = MosaicTilerFactory(backend="mosaic")
app.include_router(mosaic.router, prefix="/mosaic", tags=["Mosaic"])

# 3. Include custom WRS2 endpoints with different prefix
app.include_router(wrs2_router, prefix="/api/wrs2", tags=["WRS2 Mosaic"])


@app.get("/")
async def root():
    return {
        "message": "WRS2 COG Mosaic Server",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "cog": "/cog",
            "mosaic": "/mosaic",
            "wrs2": "/api/wrs2",
        },
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
