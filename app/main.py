"""FastAPI LLM Gateway with admission control, budgets, SQLite ledger, and Prometheus."""

import json
import time
import uuid
from typing import AsyncIterator, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from app.admission import AdmissionController
from app.config import settings
from app.engine import MockLLMEngine, OpenAICompatibleEngine
from app.ledger import UsageLedger

REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total gateway chat requests by status and reason",
    ["status_code", "reason"],
)
INFLIGHT_GAUGE = Gauge(
    "gateway_inflight_requests",
    "Current requests admitted to the upstream LLM engine",
)
TTFT_HISTOGRAM = Histogram(
    "gateway_ttft_seconds",
    "Time to first token in seconds",
    buckets=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0),
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: List[ChatMessage]
    max_tokens: int = Field(default=128, ge=1)
    stream: bool = True


def estimate_tokens(messages: List[ChatMessage]) -> int:
    """Fast deterministic token estimator (~4 chars/token)."""
    total_chars = sum(len(m.content) + len(m.role) + 4 for m in messages)
    return max(1, total_chars // 4)


def create_app(
    ledger: Optional[UsageLedger] = None,
    admission: Optional[AdmissionController] = None,
) -> FastAPI:
    app = FastAPI(title="LLM Gateway Bench", version="1.0.0")

    app.state.ledger = ledger or UsageLedger(settings.db_path)
    app.state.admission = admission or AdmissionController(
        max_concurrent_slots=settings.max_concurrent_slots,
        max_per_tenant_inflight=settings.max_per_tenant_inflight,
        retry_after_seconds=settings.retry_after_seconds,
    )

    if settings.upstream_mode == "openai_compatible":
        app.state.engine = OpenAICompatibleEngine(
            base_url=settings.upstream_base_url,
            api_key=settings.upstream_api_key,
            model=settings.upstream_model,
        )
    else:
        app.state.engine = MockLLMEngine(lambda: app.state.admission.global_inflight)

    def verify_auth(authorization: Optional[str] = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            REQUESTS_TOTAL.labels(status_code="401", reason="missing_auth").inc()
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = authorization.split(" ", 1)[1].strip()
        if token != settings.api_key:
            REQUESTS_TOTAL.labels(status_code="401", reason="invalid_auth").inc()
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/health")
    async def health() -> Dict[str, object]:
        return {
            "status": "ok",
            "inflight": app.state.admission.global_inflight,
            "max_slots": app.state.admission.max_concurrent_slots,
        }

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/usage/{tenant_id}", dependencies=[Depends(verify_auth)])
    async def tenant_usage(tenant_id: str) -> Dict[str, float]:
        return app.state.ledger.get_tenant_summary(tenant_id)

    @app.post("/v1/chat/completions", dependencies=[Depends(verify_auth)])
    async def chat_completions(
        req: ChatCompletionRequest,
        x_tenant_id: Optional[str] = Header(default=None),
    ):
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        t_start = time.perf_counter()

        if not x_tenant_id:
            REQUESTS_TOTAL.labels(status_code="400", reason="missing_tenant").inc()
            raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")

        prompt_tokens = estimate_tokens(req.messages)
        if prompt_tokens > settings.max_context_tokens:
            REQUESTS_TOTAL.labels(status_code="413", reason="context_too_large").inc()
            app.state.ledger.record(
                req_id, x_tenant_id, 413, prompt_tokens, 0, None,
                (time.perf_counter() - t_start) * 1000,
            )
            raise HTTPException(
                status_code=413,
                detail=f"Prompt tokens ({prompt_tokens}) exceed limit ({settings.max_context_tokens})",
            )

        if req.max_tokens > settings.max_output_tokens:
            REQUESTS_TOTAL.labels(status_code="400", reason="max_tokens_exceeded").inc()
            raise HTTPException(
                status_code=400,
                detail=f"max_tokens ({req.max_tokens}) exceeds cap ({settings.max_output_tokens})",
            )

        used_tokens = app.state.ledger.get_tenant_tokens(x_tenant_id)
        if used_tokens >= settings.default_tenant_token_budget:
            REQUESTS_TOTAL.labels(status_code="429", reason="token_budget_exhausted").inc()
            app.state.ledger.record(
                req_id, x_tenant_id, 429, prompt_tokens, 0, None,
                (time.perf_counter() - t_start) * 1000,
            )
            raise HTTPException(status_code=429, detail="Tenant token budget exhausted")

        decision = await app.state.admission.try_acquire(x_tenant_id)
        if not decision.admitted:
            REQUESTS_TOTAL.labels(status_code="503", reason=decision.reason).inc()
            app.state.ledger.record(
                req_id, x_tenant_id, 503, prompt_tokens, 0, None,
                (time.perf_counter() - t_start) * 1000,
            )
            return Response(
                content=json.dumps({"detail": "Gateway overloaded", "reason": decision.reason}),
                status_code=503,
                media_type="application/json",
                headers={"Retry-After": str(decision.retry_after or 1)},
            )

        INFLIGHT_GAUGE.set(app.state.admission.global_inflight)
        msg_dicts = [{"role": m.role, "content": m.content} for m in req.messages]

        if not req.stream:
            ttft_ms: Optional[float] = None
            chunks: List[str] = []
            try:
                async for token in app.state.engine.stream_chat(msg_dicts, req.max_tokens):
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t_start) * 1000
                        TTFT_HISTOGRAM.observe(ttft_ms / 1000.0)
                    chunks.append(token)
            finally:
                await app.state.admission.release(x_tenant_id)
                INFLIGHT_GAUGE.set(app.state.admission.global_inflight)

            total_ms = (time.perf_counter() - t_start) * 1000
            completion_tokens = len(chunks)
            REQUESTS_TOTAL.labels(status_code="200", reason="ok").inc()
            app.state.ledger.record(
                req_id, x_tenant_id, 200, prompt_tokens, completion_tokens, ttft_ms, total_ms
            )
            return {
                "id": req_id,
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "".join(chunks)}}
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }

        async def sse_generator() -> AsyncIterator[bytes]:
            ttft_ms: Optional[float] = None
            completion_tokens = 0
            try:
                async for token in app.state.engine.stream_chat(msg_dicts, req.max_tokens):
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t_start) * 1000
                        TTFT_HISTOGRAM.observe(ttft_ms / 1000.0)
                    completion_tokens += 1
                    payload = {
                        "id": req_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": token}}],
                    }
                    yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                REQUESTS_TOTAL.labels(status_code="200", reason="ok").inc()
            finally:
                total_ms = (time.perf_counter() - t_start) * 1000
                await app.state.admission.release(x_tenant_id)
                INFLIGHT_GAUGE.set(app.state.admission.global_inflight)
                app.state.ledger.record(
                    req_id, x_tenant_id, 200, prompt_tokens, completion_tokens, ttft_ms, total_ms
                )

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

    return app


app = create_app()
