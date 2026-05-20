from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.database import init_db
from app.services.search_worker import search_worker

load_dotenv()

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await search_worker.start()
    yield
    await search_worker.stop()


app = FastAPI(
    title="FounderHunt",
    description="Founding-engineer job aggregator with human-in-the-loop checkpoints",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(router)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
