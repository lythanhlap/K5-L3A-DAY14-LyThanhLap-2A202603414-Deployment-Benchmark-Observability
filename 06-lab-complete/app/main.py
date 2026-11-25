"""
Production AI Agent — Part 6 Final Project
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import signal
import sys
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import close_redis, redis_healthy
from utils.mock_llm import ask as llm_ask


class JsonFormatter(logging.Formatter):
    """Định dạng mỗi dòng log thành một JSON object hợp lệ trên một dòng."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "environment": settings.environment,
        }
        for key in ("event", "method", "path", "status", "latency_ms", "cost_usd", "user_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logger() -> logging.Logger:
    app_logger = logging.getLogger("agent")
    app_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    app_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    app_logger.addHandler(handler)
    app_logger.propagate = False
    return app_logger


logger = _configure_logger()
_shutting_down = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _shutting_down
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        global _shutting_down
        _shutting_down = True
        if callable(previous_sigterm):
            previous_sigterm(signum, frame)

    signal.signal(signal.SIGTERM, _on_sigterm)
    logger.info("Agent started", extra={"event": "startup"})
    try:
        yield
    finally:
        logger.info(
            "Agent shutting down gracefully",
            extra={"event": "shutdown", "sigterm": _shutting_down},
        )
        close_redis()


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "HTTP request completed",
        extra={
            "event": "request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": elapsed_ms,
        },
    )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled server error",
        exc_info=exc,
        extra={"event": "error", "method": request.method, "path": request.url.path},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default="anonymous", max_length=128)


@app.get("/health")
def health():
    """Liveness probe — luôn trả 200 khi process còn chạy."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness probe — trả 200 khi kết nối được Redis, 503 khi Redis chưa sẵn sàng."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return {"status": "ready", "redis": "connected"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)
    check_budget()

    answer = llm_ask(body.question)
    input_tokens = max(1, len(body.question) // 4)
    output_tokens = max(1, len(answer) // 4)
    cost_usd = record_cost(input_tokens, output_tokens)

    logger.info(
        "LLM question answered",
        extra={
            "event": "llm_ask",
            "user_id": body.user_id or "anonymous",
            "cost_usd": round(cost_usd, 6),
        },
    )
    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
        },
    }
