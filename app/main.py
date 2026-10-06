import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import chat, extract, health, web
from app.knowledge.milvus import close_milvus

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await close_milvus()


app = FastAPI(title="Aftersales Agent", lifespan=lifespan)
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(extract.router)
app.include_router(web.router)
