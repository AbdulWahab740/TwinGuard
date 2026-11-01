import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, check_secret
from .db import Db, SupabaseDb
from .routers import audit, auth, requests, reviews

logging.basicConfig(level=logging.INFO)


def create_app(settings: Settings | None = None, db: Db | None = None) -> FastAPI:
    settings = settings or Settings()
    check_secret(settings)
    app = FastAPI(title="TwinGuard API")
    app.state.settings = settings
    app.state.db = db or SupabaseDb.from_settings(settings)
    app.include_router(auth.router)
    app.include_router(requests.router)
    app.include_router(reviews.router)
    app.include_router(audit.router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
