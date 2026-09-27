from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap import setup_beanie
from app.database import close_motor_client, get_motor_client
from app.redis_client import close_redis_pool, get_redis_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_redis_pool()
    get_motor_client()
    await setup_beanie()
    yield
    await close_redis_pool()
    close_motor_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="LensVocab API",
        version="1.0.0",
        description="Backend cho ứng dụng học tiếng Anh LensVocab",
        lifespan=lifespan,
        docs_url="/api",
        openapi_url="/api/openapi.json",
        swagger_ui_parameters={"defaultModelsExpandDepth": -1},
    )

    from app.routers import admin, auth, flashcards, review, users, vision

    app.include_router(auth.router, tags=["Auth"])
    app.include_router(users.router, tags=["Users"])
    app.include_router(admin.router, tags=["Admin"])
    app.include_router(vision.router, prefix="/vision", tags=["Vision"])
    app.include_router(flashcards.router, tags=["Flashcards"])
    app.include_router(review.router, tags=["Review"])

    return app


app = create_app()
