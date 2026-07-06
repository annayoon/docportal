from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .routers import documents, search, wiki

app = FastAPI(title="DocPortal — 전사 문서 포털")

init_db()

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(wiki.router)
