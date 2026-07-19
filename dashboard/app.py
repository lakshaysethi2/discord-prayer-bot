from fastapi import FastAPI
from dashboard.prayers_routes import router

app = FastAPI(title="Discord Prayer Bot Dashboard")
app.include_router(router)
