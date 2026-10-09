import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api import chat, conversations, extract, faith_cases, health, knowledge, refunds, tickets, web
from app.context.budget import get_budget, startup_check
from app.context.summarizer import get_runner
from app.graph.builder import open_graph
from app.graph.nodes.agent import measure_system_tokens
from app.knowledge.milvus import close_milvus
from app.knowledge.rerank import close_rerank

LOG_PATH = Path(__file__).resolve().parent.parent / "log" / "app.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def setup_file_logging(path: Path | None = None) -> None:
    """给根 logger 加文件输出。重复调用不重复添加。"""
    path = (path or LOG_PATH).resolve()
    root = logging.getLogger()
    if any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == path
           for handler in root.handlers):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_file_logging(LOG_PATH)
    startup_check(get_budget(), measure_system_tokens())
    try:
        async with open_graph():
            yield
    finally:
        await get_runner().cancel_all()
        try:
            await close_milvus()
        finally:
            await close_rerank()


app = FastAPI(title="Aftersales Agent", lifespan=lifespan)
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(tickets.router)
app.include_router(refunds.router)
app.include_router(extract.router)
app.include_router(knowledge.router)
app.include_router(faith_cases.router)
app.include_router(web.router)
