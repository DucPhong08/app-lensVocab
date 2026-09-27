from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

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
        lifespan=lifespan,
        docs_url="/api",
        swagger_ui_parameters={
            "defaultModelsExpandDepth": -1,
            "displayRequestDuration": True,
        },
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = (time.perf_counter() - start_time) * 1000
        response.headers["X-Process-Time"] = f"{process_time:.2f}ms"
        return response

    @app.get("/health", tags=["Health"])
    async def health_check():
        return {"status": "ok"}

    from app.routers import admin, auth, flashcards, review, users, vision

    app.include_router(auth.router, tags=["Auth"])
    app.include_router(users.router, tags=["Users"])
    app.include_router(admin.router, tags=["Admin"])
    app.include_router(vision.router, prefix="/vision", tags=["Vision"])
    app.include_router(flashcards.router, tags=["Flashcards"])
    app.include_router(review.router, tags=["Review"])

    return app


app = create_app()
