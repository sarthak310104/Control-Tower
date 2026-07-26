import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import Base, engine
from app.routers import projects, agents, claims, messages, activity
from app.services.reaper import start_reaper, stop_reaper

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Agent Coordinator",
    description=(
        "Shared coordination service for multiple AI coding agents working on the "
        "same codebase: function/AST-level claims to avoid overwriting each other, "
        "point-to-point and broadcast messaging, and a unified activity feed for "
        "catch-up context."
    ),
    version="0.1.0",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    start_reaper()


@app.on_event("shutdown")
def on_shutdown():
    stop_reaper()


app.include_router(projects.router)
app.include_router(agents.router)
app.include_router(claims.router)
app.include_router(messages.router)
app.include_router(activity.router)


@app.get("/health")
def health():
    return {"status": "ok"}
