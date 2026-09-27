from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.bootstrap import setup_beanie
from app.database import close_motor_client, get_motor_client
from app.redis_client import close_redis_pool, get_redis_pool

logger = logging.getLogger(__name__)


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

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        error_phrase = (
            HTTPStatus(exc.status_code).phrase
            if exc.status_code in HTTPStatus._value2member_map_
            else "HTTP_ERROR"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status_code": exc.status_code,
                "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                "error": error_phrase,
                "data": None,
                "detail": exc.detail,
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "status_code": 422,
                "message": "VALIDATION_ERROR",
                "error": "Unprocessable Entity",
                "data": exc.errors(),
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("unhandled_server_exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "status_code": 500,
                "message": "INTERNAL_SERVER_ERROR",
                "error": exc.__class__.__name__,
                "data": None,
                "detail": "INTERNAL_SERVER_ERROR",
            },
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
