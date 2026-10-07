import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import chat, extract, faith_cases, health, knowledge, tickets, web
from app.graph.builder import open_graph
from app.knowledge.milvus import close_milvus
from app.knowledge.rerank import close_rerank

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with open_graph():
            yield
    finally:
        try:
            await close_milvus()
        finally:
            await close_rerank()


app = FastAPI(title="Aftersales Agent", lifespan=lifespan)
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(tickets.router)
app.include_router(extract.router)
app.include_router(knowledge.router)
app.include_router(faith_cases.router)
app.include_router(web.router)
