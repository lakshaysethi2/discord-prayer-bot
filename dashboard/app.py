from fastapi import FastAPI
from dashboard.prayers_routes import router
from dashboard.health import router as health_router

app = FastAPI(title="Discord Prayer Bot Dashboard")
app.include_router(router)
app.include_router(health_router)
