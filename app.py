from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from database import create_db_and_tables
from scheduler import retry_pending_notifications
from routers import web, webhooks

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    try:
        retry_pending_notifications()
    except Exception as e:
        print(f"[Startup] Retry notification check error: {e}")
    yield

app = FastAPI(title="Workshop OS - Web Dashboard", version="1.1.0", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include Routers
app.include_router(web.router)
app.include_router(webhooks.router)