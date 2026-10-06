import logging

from fastapi import FastAPI

from app.api import chat, extract, health, web

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Aftersales Agent")
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(extract.router)
app.include_router(web.router)
