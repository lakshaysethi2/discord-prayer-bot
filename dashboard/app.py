from contextlib import asynccontextmanager

from fastapi import FastAPI

from dashboard.auth import require_configured
from dashboard.prayers_routes import router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    require_configured()
    yield

app = FastAPI(title="Discord Prayer Bot Dashboard", lifespan=lifespan)
app.include_router(router)
