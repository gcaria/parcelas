from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from titiler.core.factory import TilerFactory
from titiler.mosaic.factory import MosaicTilerFactory

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

# 2. Create the Mosaic endpoints (for your WRS2 mosaicJSON)
mosaic = MosaicTilerFactory(backend="mosaic")
app.include_router(mosaic.router, prefix="/mosaic", tags=["Mosaic"])


@app.get("/health")
def health():
    return {"status": "healthy"}
