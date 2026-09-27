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
    )

    from app.routers import admin, auth, flashcards, review, users, vision

    app.include_router(auth.router, prefix="/api/v1", tags=["Auth"])
    app.include_router(users.router, prefix="/api/v1", tags=["Users"])
    app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])
    app.include_router(vision.router, prefix="/api/v1", tags=["Vision"])
    app.include_router(flashcards.router, prefix="/api/v1", tags=["Flashcards"])
    app.include_router(review.router, prefix="/api/v1", tags=["Review"])

    return app


app = create_app()