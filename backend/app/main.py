"""FastAPI application entry point.

Run (from backend/):  .venv\\Scripts\\uvicorn app.main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep
from app.api.v1 import api_router
from app.config import get_settings
from app.db import engine

logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield                      # the app runs here
    await engine.dispose()     # on shutdown: close database connections cleanly


def create_app() -> FastAPI:
    settings = get_settings()
    is_prod = settings.app_env == "production"

    app = FastAPI(
        title=settings.app_name,
        version="0.4.0",
        description="REST API for the WhatsApp AI Business Agent dashboard and n8n workflows.",
        lifespan=lifespan,
        # Interactive docs are great in development, but they reveal the API map: hide in production.
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )

    # CORS: browsers only let the dashboard's own origin call this API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        # A database rule was broken (e.g. duplicate). Never leak SQL details to the client.
        logger.warning("IntegrityError on %s %s: %s", request.method, request.url.path, exc.orig)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT,
                            content={"detail": "This conflicts with existing data"})

    @app.get("/health", tags=["health"])
    async def health(session: SessionDep):
        """Used by Docker and monitoring: is the API up and can it reach the database?"""
        try:
            await session.execute(text("SELECT 1"))
            db = "ok"
        except Exception:  # noqa: BLE001 - health must never crash
            db = "unreachable"
        code = status.HTTP_200_OK if db == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(status_code=code, content={"status": "ok" if db == "ok" else "degraded",
                                                       "database": db, "version": app.version})

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
