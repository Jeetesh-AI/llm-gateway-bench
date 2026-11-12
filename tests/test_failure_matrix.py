"""Failure & policy matrix tests for the LLM Gateway."""

import asyncio
import json
import os
import tempfile
from typing import Tuple

import httpx
import pytest

from app.admission import AdmissionController
from app.config import settings
from app.ledger import UsageLedger
from app.main import create_app


def make_test_app(
    max_slots: int = 10,
    max_per_tenant: int = 4,
) -> Tuple[object, UsageLedger]:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    ledger = UsageLedger(db_path=db_path)
    admission = AdmissionController(
        max_concurrent_slots=max_slots,
        max_per_tenant_inflight=max_per_tenant,
        retry_after_seconds=1,
    )
    app = create_app(ledger=ledger, admission=admission)
    return app, ledger


HEADERS = {
    "Authorization": f"Bearer {settings.api_key}",
    "X-Tenant-ID": "tenant-alpha",
}


@pytest.mark.asyncio
async def test_health_and_metrics() -> None:
    app, ledger = make_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        h = await client.get("/health")
        assert h.status_code == 200
        assert h.json()["status"] == "ok"

        m = await client.get("/metrics")
        assert m.status_code == 200
        assert "gateway_requests_total" in m.text
    ledger.close()


@pytest.mark.asyncio
async def test_auth_missing_and_invalid() -> None:
    app, ledger = make_test_app()
    transport = httpx.ASGITransport(app=app)
    payload = {"messages": [{"role": "user", "content": "hi"}], "stream": False}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/v1/chat/completions", json=payload)
        assert r1.status_code == 401

        r2 = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer wrong-key", "X-Tenant-ID": "t1"},
            json=payload,
        )
        assert r2.status_code == 401
    ledger.close()


@pytest.mark.asyncio
async def test_missing_tenant_header() -> None:
    app, ledger = make_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        assert r.status_code == 400
    ledger.close()


@pytest.mark.asyncio
async def test_context_and_output_bounds() -> None:
    app, ledger = make_test_app()
    transport = httpx.ASGITransport(app=app)
    oversized_prompt = "x " * (settings.max_context_tokens * 3)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": oversized_prompt}], "stream": False},
        )
        assert r1.status_code == 413

        r2 = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": settings.max_output_tokens + 100,
                "stream": False,
            },
        )
        assert r2.status_code == 400
    ledger.close()


@pytest.mark.asyncio
async def test_tenant_token_budget_exhausted() -> None:
    app, ledger = make_test_app()
    ledger.record("seed", "tenant-capped", 200, settings.default_tenant_token_budget, 10, 100.0, 200.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}", "X-Tenant-ID": "tenant-capped"},
            json={"messages": [{"role": "user", "content": "hello"}], "stream": False},
        )
        assert r.status_code == 429
    ledger.close()


@pytest.mark.asyncio
async def test_streaming_sse_and_ledger_recording() -> None:
    app, ledger = make_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        tokens = []
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": "explain admission control"}], "stream": True},
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    chunk = json.loads(line[6:])
                    tokens.append(chunk["choices"][0]["delta"]["content"])
        assert len(tokens) > 0

        usage = await client.get("/v1/usage/tenant-alpha", headers=HEADERS)
        assert usage.status_code == 200
        assert usage.json()["succeeded"] == 1
        assert usage.json()["total_tokens"] > 0
    ledger.close()


@pytest.mark.asyncio
async def test_admission_refusal_with_retry_after_and_tenant_fairness() -> None:
    app, ledger = make_test_app(max_slots=4, max_per_tenant=2)
    transport = httpx.ASGITransport(app=app)

    async def send_req(client: httpx.AsyncClient, tenant: str) -> httpx.Response:
        return await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}", "X-Tenant-ID": tenant},
            json={"messages": [{"role": "user", "content": "load test"}], "stream": False},
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        noisy = [send_req(client, "noisy-tenant") for _ in range(4)]
        fair = [send_req(client, "fair-tenant")]
        results = await asyncio.gather(*noisy, *fair)

        noisy_codes = [r.status_code for r in results[:4]]
        fair_code = results[4].status_code

        assert 503 in noisy_codes
        for r in results[:4]:
            if r.status_code == 503:
                assert r.headers.get("Retry-After") == "1"
        assert fair_code == 200
    ledger.close()
