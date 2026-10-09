import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.config import settings
from app.migrate import run_migrations
from app.routers import projects, agents, claims, messages, activity, files, demo, reconcile
from app.services.reaper import start_reaper, stop_reaper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("control_tower")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.auto_migrate:
        run_migrations()
    if not settings.auth_enabled:
        logger.warning("auth is OFF (CT_ADMIN_TOKEN unset): anyone who can reach this port can act as any agent")
    start_reaper()
    yield
    stop_reaper()


app = FastAPI(
    title="Control Tower",
    description=(
        "Shared coordination service for multiple AI coding agents working on the "
        "same codebase: function/AST-level claims to avoid overwriting each other, "
        "point-to-point and broadcast messaging, and a unified activity feed for "
        "catch-up context."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(projects.router)
app.include_router(agents.router)
app.include_router(claims.router)
app.include_router(messages.router)
app.include_router(activity.router)
app.include_router(files.router)
app.include_router(demo.router)
app.include_router(reconcile.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "claim_ttl_seconds": settings.claim_ttl_seconds,
        "max_claim_age_seconds": settings.max_claim_age_seconds,
        "auth_enabled": config.settings.auth_enabled,
        "demo_enabled": settings.demo_enabled,
        "demo_project": settings.demo_project,
    }


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")