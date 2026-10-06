import logging

from fastapi import FastAPI

from app.api import chat, health

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Aftersales Agent")
app.include_router(health.router)
app.include_router(chat.router)
