import asyncio
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from app.routes import router
from app.scheduler import create_scheduler
from app.bot import start_polling

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = create_scheduler()
    scheduler.start()
    poll_task = asyncio.create_task(start_polling())
    yield
    poll_task.cancel()
    await asyncio.gather(poll_task, return_exceptions=True)
    scheduler.shutdown()


app = FastAPI(title="Trade Signals", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
