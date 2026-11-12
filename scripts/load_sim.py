"""Multi-user async load simulator measuring SLO attainment and tail latency."""

import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.admission import AdmissionController
from app.config import settings
from app.ledger import UsageLedger
from app.main import create_app

SLO_TTFT_MS = 1500.0
USER_LEVELS = [5, 10, 15, 20, 25, 30]
TURNS_PER_USER = 3


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return round(ordered[idx], 1)


async def run_user_session(
    client: httpx.AsyncClient,
    user_idx: int,
    slo_ms: float,
) -> List[Dict[str, object]]:
    tenant_id = f"tenant-{user_idx % 8}"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "X-Tenant-ID": tenant_id,
    }
    records: List[Dict[str, object]] = []

    for turn in range(TURNS_PER_USER):
        # Staggered Poisson-like arrival
        await asyncio.sleep((user_idx % 5) * 0.04)
        first_send = time.perf_counter()
        retries = 0
        ttft_ms = None
        succeeded = False

        while retries <= 4:
            try:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    headers=headers,
                    json={
                        "messages": [
                            {"role": "user", "content": f"Turn {turn}: summarize gateway metrics"}
                        ],
                        "max_tokens": 24,
                        "stream": True,
                    },
                    timeout=15.0,
                ) as resp:
                    if resp.status_code == 503:
                        retry_after = float(resp.headers.get("Retry-After", "0.25"))
                        retries += 1
                        await asyncio.sleep(min(0.35, retry_after * 0.25))
                        continue
                    if resp.status_code != 200:
                        break
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line[6:] != "[DONE]":
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - first_send) * 1000.0
                            break
                    succeeded = True
                    break
            except httpx.HTTPError:
                break

        records.append(
            {
                "succeeded": succeeded,
                "retries": retries,
                "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
                "slo_met": bool(succeeded and ttft_ms is not None and ttft_ms <= slo_ms),
            }
        )
    return records


async def benchmark_level(users: int) -> Dict[str, object]:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    ledger = UsageLedger(db_path=db_path)
    admission = AdmissionController(
        max_concurrent_slots=10,
        max_per_tenant_inflight=4,
        retry_after_seconds=1,
    )
    app = create_app(ledger=ledger, admission=admission)
    transport = httpx.ASGITransport(app=app)

    t0 = time.perf_counter()
    async with httpx.AsyncClient(transport=transport, base_url="http://bench") as client:
        sessions = await asyncio.gather(
            *[run_user_session(client, idx, SLO_TTFT_MS) for idx in range(users)]
        )
    elapsed_s = time.perf_counter() - t0
    ledger.close()
    os.remove(db_path)

    all_msgs = [msg for session in sessions for msg in session]
    ttfts = [float(m["ttft_ms"]) for m in all_msgs if m["ttft_ms"] is not None]
    slo_met = sum(1 for m in all_msgs if m["slo_met"])
    retries = sum(int(m["retries"]) for m in all_msgs)
    errors = sum(1 for m in all_msgs if not m["succeeded"])

    return {
        "concurrent_users": users,
        "total_messages": len(all_msgs),
        "slo_attainment_pct": round((slo_met / len(all_msgs)) * 100.0, 1),
        "p50_ttft_ms": round(statistics.median(ttfts), 1) if ttfts else 0.0,
        "p95_ttft_ms": percentile(ttfts, 95),
        "retries_503": retries,
        "errors": errors,
        "duration_s": round(elapsed_s, 2),
    }


async def main() -> None:
    results = []
    for users in USER_LEVELS:
        row = await benchmark_level(users)
        results.append(row)
        print(
            f"users={row['concurrent_users']:2d} | "
            f"SLO={row['slo_attainment_pct']:5.1f}% | "
            f"p50={row['p50_ttft_ms']:6.1f}ms | "
            f"p95={row['p95_ttft_ms']:6.1f}ms | "
            f"503_retries={row['retries_503']:2d} | "
            f"errors={row['errors']}"
        )

    out_dir = ROOT / "docs" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "load_test.json", "w", encoding="utf-8") as f:
        json.dump({"slo_target_ms": SLO_TTFT_MS, "levels": results}, f, indent=2)

    md_lines = [
        "# Measured Load Test Results",
        "",
        f"SLO target: **first token within {int(SLO_TTFT_MS)} ms** of first send (retries included).",
        "",
        "| Concurrent Users | Messages | SLO Attainment | p50 TTFT (ms) | p95 TTFT (ms) | 503 Retries | Errors |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for r in results:
        md_lines.append(
            f"| {r['concurrent_users']} | {r['total_messages']} | {r['slo_attainment_pct']}% | "
            f"{r['p50_ttft_ms']} | {r['p95_ttft_ms']} | {r['retries_503']} | {r['errors']} |"
        )
    with open(ROOT / "docs" / "RESULTS.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
